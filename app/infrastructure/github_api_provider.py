"""GithubProvider backed by the GitHub REST events API, the search API
(near real time, for recent PR activity) and GraphQL (PR titles/states).

Authentication: a token from GITHUB_TOKEN / GH_TOKEN or,
locally, the user's `gh auth token`. Classic PAT scopes needed:
`repo` (private repos) and `read:org`.

Every GET is conditional: the provider remembers each URL's ETag and body,
and a 304 answer (which does not count against the rate limit) reuses the
remembered body. That keeps 60 s polling of several feeds cheap. PR titles
and states come from one batched GraphQL query, cached for a few minutes.
"""

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from app.domain.common import EPOCH, parse_timestamp
from app.domain.errors import GithubError, GithubNotFoundError
from app.domain.github import (
    ActivitySnapshot,
    FeedSpec,
    GithubEvent,
    GithubEventBatch,
    GithubProvider,
    GithubRepo,
    ItemState,
    PullRequestInfo,
    Quota,
    TokenKind,
    TokenStatus,
)
from app.domain.github_rules import pull_url
from app.infrastructure.github_event_parsing import events_from_search_pull, parse_event

API_URL = "https://api.github.com"
PER_PAGE = 100
GRAPHQL_CHUNK = 40
# The events API never serves more than 300 events per source (its last page
# then has no "next" link even though older history exists). Pages may hold
# 99 or 98 items, so "reached the cap" is judged with a little slack.
EVENTS_API_CAP = 300
EVENTS_CAP_SLACK = 15
REPO_FETCH_WORKERS = 8

README_HINT = "See the GitHub tab section of the README."
CLASSIC_HINT = "A classic token with the `repo` and `read:org` scopes is the simplest setup."
FINE_GRAINED_HINT = (
    "A fine-grained token only works for one owner: set Resource owner to the organization,"
    " give it access to the repositories you follow and grant Organization permissions →"
    " Events (read) and Repository permissions → Metadata (read) and Pull requests (read)."
    " The organization may have to approve it, or may not allow fine-grained tokens at all."
)
_ORG_SCOPES = {"read:org", "write:org", "admin:org"}
_TOKEN_PREFIXES = {
    "ghp_": TokenKind.CLASSIC,
    "github_pat_": TokenKind.FINE_GRAINED,
    "gho_": TokenKind.OAUTH,
}


def token_kind(token: str) -> TokenKind:
    if not token:
        return TokenKind.NONE
    for prefix, kind in _TOKEN_PREFIXES.items():
        if token.startswith(prefix):
            return kind
    return TokenKind.OTHER


def _missing_scopes(scopes: tuple[str, ...]) -> list[str]:
    missing = []
    if "repo" not in scopes:
        missing.append("repo")
    if not _ORG_SCOPES & set(scopes):
        missing.append("read:org")
    return missing


# GitHub rejects search queries longer than 256 characters; repo qualifiers
# are split across several searches below that.
SEARCH_QUERY_MAX = 240
SEARCH_PAGE_SIZE = 50
SEARCH_MAX_PAGES = 2
SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: PAGE_SIZE, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url state isDraft createdAt mergedAt closedAt
        headRefName baseRefName
        repository { nameWithOwner }
        author { __typename login avatarUrl }
        mergedBy { __typename login avatarUrl }
        reviews(last: 20) {
          nodes { state submittedAt url body author { __typename login avatarUrl } }
        }
      }
    }
  }
}
""".replace("PAGE_SIZE", str(SEARCH_PAGE_SIZE))  # a placeholder keeps the GraphQL braces unescaped


def resolve_github_token(configured: str = "") -> str:
    """The configured token, else the token of the local `gh` login, else ""."""
    if configured:
        return configured
    gh = shutil.which("gh")
    if not gh:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments, gh resolved from PATH
            [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class GithubApiProvider(GithubProvider):
    def __init__(
        self,
        token_source: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_URL,
        timeout_seconds: float = 15.0,
        org_pages: int = 3,
        repo_pages: int = 2,
        pr_cache_seconds: float = 300.0,
        search_cache_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._token_source = token_source
        self._token = ""
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout_seconds,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "opsdeck-dashboard",
            },
        )
        self._org_pages = org_pages
        self._repo_pages = repo_pages
        self._pr_cache_seconds = pr_cache_seconds
        self._search_cache_seconds = search_cache_seconds
        self._search_cache: dict[str, tuple[float, list[dict]]] = {}
        self._clock = clock
        self._lock = threading.Lock()
        self._etags: dict[str, tuple[str, object, bool]] = {}
        self._pr_cache: dict[tuple[str, int], tuple[float, PullRequestInfo]] = {}
        self._viewer = ""
        self._rate: tuple[int, int] | None = None

    # ----- port --------------------------------------------------------

    def is_available(self) -> bool:
        return bool(self._ensure_token(raise_if_missing=False))

    def token_status(self) -> TokenStatus:
        token = self._ensure_token(raise_if_missing=False)
        kind = token_kind(token)
        if kind is TokenKind.NONE:
            return TokenStatus(
                kind, problems=[f"No GitHub token: set GITHUB_TOKEN or run `gh auth login`. {README_HINT}"]
            )
        try:
            response = self._send("GET", "/user")
        except GithubError as error:
            return TokenStatus(kind, problems=[str(error)])
        login = (response.json() or {}).get("login", "")
        self._viewer = self._viewer or login
        # classic and OAuth tokens list their scopes; fine-grained ones do not
        header = response.headers.get("x-oauth-scopes")
        scopes = (
            None if header is None else tuple(scope.strip() for scope in header.split(",") if scope.strip())
        )
        status = TokenStatus(kind, login=login, scopes=scopes)
        if scopes is not None and (missing := _missing_scopes(scopes)):
            status.problems.append(
                f"The token is missing the {' and '.join(f'`{s}`' for s in missing)}"
                f" scope{'s' if len(missing) > 1 else ''}, so private repositories and"
                f" organization activity stay hidden. {CLASSIC_HINT}"
            )
        if kind is TokenKind.FINE_GRAINED:
            status.notes.append(FINE_GRAINED_HINT)
        return status

    def activity(self, spec: FeedSpec, realtime_since: datetime) -> ActivitySnapshot:
        viewer = self.viewer()
        batches, warnings = self._batches(spec)
        try:
            realtime = self.recent_pull_request_events(spec.org, list(spec.repos), realtime_since)
        except GithubError as error:
            warnings.append(f"Real-time PR search unavailable: {error}")
            realtime = []
        return ActivitySnapshot(
            viewer=viewer,
            batches=batches,
            realtime=realtime,
            warnings=warnings,
            quota=self.quota(),
        )

    def _batches(self, spec: FeedSpec) -> tuple[list[GithubEventBatch], list[str]]:
        """The org's own feed, or one batch per repo read in parallel. A repo
        that fails becomes a warning; the read fails only when all of them do."""
        if not spec.repos:
            batch, public_only = self._org_batch(spec.org)
            return [batch], [self._public_only_warning(spec.org)] if public_only else []

        def fetch(repo: str):
            try:
                return self.repo_events(spec.org, repo), None
            except GithubNotFoundError:
                hint = (
                    " Fine-grained tokens only see the repositories they were granted."
                    if token_kind(self._token) is TokenKind.FINE_GRAINED
                    else ""
                )
                missing = GithubError(f"{spec.org}/{repo} does not exist or the token cannot see it.{hint}")
                return GithubEventBatch([]), missing
            except GithubError as error:
                return GithubEventBatch([]), error

        workers = min(REPO_FETCH_WORKERS, len(spec.repos))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(fetch, spec.repos))
        errors = [error for _, error in results if error is not None]
        if errors and len(errors) == len(results):
            raise errors[0]
        return [batch for batch, _ in results], [str(error) for error in errors]

    def quota(self) -> Quota | None:
        """What was left of the REST allowance as of the last response."""
        return Quota(*self._rate) if self._rate else None

    def viewer(self) -> str:
        if not self._viewer:
            user = self._get("/user")
            self._viewer = user.get("login", "") if isinstance(user, dict) else ""
        return self._viewer

    def org_events(self, org: str) -> GithubEventBatch:
        return self._org_batch(org)[0]

    def _org_batch(self, org: str) -> tuple[GithubEventBatch, bool]:
        """The org "dashboard" of the viewer, which includes private repos, or,
        when the token cannot read it, the org's public events (flagged True)."""
        try:
            return self._event_batch(f"/users/{self.viewer()}/events/orgs/{org}", self._org_pages), False
        except GithubNotFoundError:
            return self._event_batch(f"/orgs/{org}/events", self._org_pages), True

    def _public_only_warning(self, org: str) -> str:
        if token_kind(self._token) is TokenKind.FINE_GRAINED:
            hint = FINE_GRAINED_HINT
        else:
            hint = (
                f"Check that you are a member of {org} and that the token has the `repo`"
                f" and `read:org` scopes; if {org} uses SAML single sign-on, authorize"
                " the token for it."
            )
        return f"Only public events of {org} are shown: the token cannot read its private activity. {hint}"

    def repo_events(self, org: str, repo: str) -> GithubEventBatch:
        return self._event_batch(f"/repos/{org}/{repo}/events", self._repo_pages)

    def _event_batch(self, path: str, max_pages: int) -> GithubEventBatch:
        raw, more_pages = self._paged_with_more(path, max_pages)
        at_api_cap = len(raw) >= EVENTS_API_CAP - EVENTS_CAP_SLACK
        return GithubEventBatch([parse_event(item) for item in raw], more_pages or at_api_cap)

    def recent_pull_request_events(self, org: str, repos: list[str], since: datetime) -> list[GithubEvent]:
        # whole-minute "since" keeps the search query (and its cache key)
        # stable across refreshes within the same minute
        floor = since.astimezone(UTC).replace(second=0, microsecond=0)
        stamp = floor.strftime("%Y-%m-%dT%H:%M:%SZ")
        events: list[GithubEvent] = []
        seen: set[str] = set()
        for query in search_queries(org, repos, stamp):
            for pull in self._search_pulls(query):
                for event in events_from_search_pull(pull, floor):
                    if event.id not in seen:
                        seen.add(event.id)
                        events.append(event)
        return sorted(events, key=lambda e: e.created_at, reverse=True)

    def _search_pulls(self, query: str) -> list[dict]:
        cached = self._search_cache.get(query)
        if cached and cached[0] > self._clock():
            return cached[1]
        pulls: list[dict] = []
        after = None
        for _ in range(SEARCH_MAX_PAGES):
            body = self._graphql(SEARCH_QUERY, {"q": query, "after": after})
            search = body.get("search") or {}
            pulls.extend(node for node in search.get("nodes") or [] if node)
            page = search.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
        with self._lock:
            self._search_cache[query] = (self._clock() + self._search_cache_seconds, pulls)
        return pulls

    def _graphql(self, query: str, variables: dict) -> dict:
        response = self._send("POST", "/graphql", json={"query": query, "variables": variables})
        body = response.json()
        data = body.get("data")
        if data is None:
            errors = "; ".join(e.get("message", "") for e in body.get("errors", []))
            raise GithubError(f"GitHub GraphQL failed: {errors or 'no data'}")
        return data

    def org_repos(self, org: str) -> list[GithubRepo]:
        try:
            raw = self._paged(f"/orgs/{org}/repos", 10, type="all", sort="pushed")
        except GithubNotFoundError:  # a user account, not an org
            raw = self._paged(f"/users/{org}/repos", 10, type="owner", sort="pushed")
        repos = [
            GithubRepo(
                name=item.get("name", ""),
                full_name=item.get("full_name", ""),
                private=bool(item.get("private")),
                archived=bool(item.get("archived")),
                description=item.get("description") or "",
                pushed_at=parse_timestamp(item.get("pushed_at")),
                url=item.get("html_url", ""),
            )
            for item in raw
        ]
        return sorted(repos, key=lambda repo: repo.pushed_at or EPOCH, reverse=True)

    def pull_requests(self, refs: list[tuple[str, int]]) -> dict[tuple[str, int], PullRequestInfo]:
        now = self._clock()
        result: dict[tuple[str, int], PullRequestInfo] = {}
        missing: list[tuple[str, int]] = []
        for ref in dict.fromkeys(refs):
            cached = self._pr_cache.get(ref)
            if cached and cached[0] > now:
                result[ref] = cached[1]
            else:
                missing.append(ref)
        for start in range(0, len(missing), GRAPHQL_CHUNK):
            fetched = self._query_pull_requests(missing[start : start + GRAPHQL_CHUNK])
            expires = self._clock() + self._pr_cache_seconds
            with self._lock:
                for ref, info in fetched.items():
                    self._pr_cache[ref] = (expires, info)
            result.update(fetched)
        return result

    # ----- HTTP --------------------------------------------------------

    def _ensure_token(self, raise_if_missing: bool = True) -> str:
        if not self._token:
            self._token = self._token_source() or ""
        if not self._token and raise_if_missing:
            raise GithubError("No GitHub token: set GITHUB_TOKEN or run `gh auth login`")
        return self._token

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._ensure_token()}"
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise GithubError(f"Could not reach GitHub: {error}") from None
        self._track_rate(response)
        if response.status_code == 401:
            self._token = ""  # e.g. rotated gh token: resolve again next time
            raise GithubError(
                "GitHub rejected the token: it is invalid, expired or revoked."
                f" Create a new one and update GITHUB_TOKEN. {README_HINT}"
            )
        if response.status_code in (403, 429) and self._rate_exhausted(response):
            raise GithubError(self._rate_message(response))
        if response.status_code == 404:
            raise GithubNotFoundError(f"Not found on GitHub: {path}")
        if response.status_code == 403:
            raise GithubError(_forbidden_message(_message(response)))
        if response.status_code >= 400:
            raise GithubError(f"GitHub answered {response.status_code}: {_message(response)}")
        return response

    def _get(self, path: str, **params) -> object:
        return self._get_with_next(path, **params)[0]

    def _get_with_next(self, path: str, **params) -> tuple[object, bool]:
        """Body plus whether the Link header announces a next page."""
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self._etags.get(key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        response = self._send("GET", path, params=params, headers=headers)
        if response.status_code == 304 and cached:
            return cached[1], cached[2]
        data = response.json()
        has_next = "next" in response.links
        etag = response.headers.get("etag")
        if etag:
            with self._lock:
                self._etags[key] = (etag, data, has_next)
        return data, has_next

    def _paged(self, path: str, max_pages: int, **params) -> list[dict]:
        return self._paged_with_more(path, max_pages, **params)[0]

    def _paged_with_more(self, path: str, max_pages: int, **params) -> tuple[list[dict], bool]:
        """Items of up to `max_pages` pages, and whether more pages remained.

        GitHub may return fewer than per_page items on a page that still has a
        successor, so this follows the Link header instead of the page size.
        """
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            batch, has_next = self._get_with_next(path, per_page=PER_PAGE, page=page, **params)
            if not isinstance(batch, list):
                return items, False
            items.extend(batch)
            if not has_next:
                return items, False
        return items, True

    def _track_rate(self, response: httpx.Response) -> None:
        resource = response.headers.get("x-ratelimit-resource", "core")
        remaining = response.headers.get("x-ratelimit-remaining")
        limit = response.headers.get("x-ratelimit-limit")
        if resource == "core" and remaining is not None and limit is not None:
            self._rate = (int(remaining), int(limit))

    @staticmethod
    def _rate_exhausted(response: httpx.Response) -> bool:
        return (
            response.status_code == 429
            or response.headers.get("x-ratelimit-remaining") == "0"
            or "rate limit" in _message(response).lower()
        )

    @staticmethod
    def _rate_message(response: httpx.Response) -> str:
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            minutes = max(1, round((int(reset) - time.time()) / 60))
            return f"GitHub rate limit exhausted; resets in ~{minutes} min"
        return "GitHub rate limit exhausted; try again in a few minutes"

    # ----- GraphQL -----------------------------------------------------

    def _query_pull_requests(self, refs: list[tuple[str, int]]) -> dict[tuple[str, int], PullRequestInfo]:
        by_repo: dict[str, list[int]] = {}
        for repo, number in refs:
            by_repo.setdefault(repo, []).append(number)
        parts, aliases = [], {}
        for r_index, (repo, numbers) in enumerate(by_repo.items()):
            owner, _, name = repo.partition("/")
            fields = []
            for number in numbers:
                alias = f"p{number}"
                aliases[(f"r{r_index}", alias)] = (repo, number)
                fields.append(f"{alias}: pullRequest(number: {int(number)}) {{ title state isDraft url }}")
            parts.append(
                f"r{r_index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)})"
                " { " + " ".join(fields) + " }"
            )
        data = self._graphql("query { " + " ".join(parts) + " }", {})
        result = {}
        for (repo_alias, pr_alias), (repo, number) in aliases.items():
            node = (data.get(repo_alias) or {}).get(pr_alias)
            if not node:  # number is an issue, or the PR is gone
                continue
            state = (node.get("state") or "").lower()
            if state == "open" and node.get("isDraft"):
                state = "draft"
            result[(repo, number)] = PullRequestInfo(
                title=node.get("title") or "",
                state=ItemState(state) if state in ItemState else None,
                url=node.get("url") or pull_url(repo, number),
            )
        return result


def _forbidden_message(message: str) -> str:
    """GitHub's 403 text plus what to do about the usual causes."""
    lower = message.lower()
    if "saml" in lower:
        hint = (
            "The organization uses SAML single sign-on: open https://github.com/settings/tokens,"
            " choose Configure SSO next to the token and authorize the organization."
        )
    elif "fine-grained" in lower:
        hint = (
            "The organization restricts fine-grained tokens: ask an owner to allow or approve"
            f" it, or use a classic token. {CLASSIC_HINT}"
        )
    else:
        hint = README_HINT
    return f"GitHub refused the request: {message}. {hint}"


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    return (body.get("message") if isinstance(body, dict) else "") or response.reason_phrase


def search_queries(org: str, repos: list[str], since: str) -> list[str]:
    """Search queries for PRs updated since `since`: one for the whole org,
    or repo qualifiers (OR-ed by GitHub) packed under SEARCH_QUERY_MAX."""
    base = f"is:pr updated:>={since}"
    if not repos:
        return [f"org:{org} {base}"]
    queries, current = [], base
    for repo in repos:
        qualifier = f" repo:{org}/{repo}"
        if current != base and len(current) + len(qualifier) > SEARCH_QUERY_MAX:
            queries.append(current)
            current = base
        current += qualifier
    queries.append(current)
    return queries
