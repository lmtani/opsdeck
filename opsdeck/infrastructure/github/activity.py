"""What the timeline reads: the events API (the org feed or each repo), the
GraphQL PR search for near-real-time openings, merges and reviews, and the
titles and states of the PRs the events mention.

Each source's event window is remembered: a refresh reads pages only until it
meets an event it already holds (usually page 1 alone, often a free 304) and
prepends what is new, instead of reading every page again. PR searches are
cached for a minute and PR titles for a few.
"""

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from opsdeck.domain.errors import GithubError, GithubNotFoundError
from opsdeck.domain.github import (
    ActivitySnapshot,
    FeedSpec,
    GithubEvent,
    GithubEventBatch,
    ItemState,
    PullRequestInfo,
    TokenKind,
)
from opsdeck.domain.github_rules import pull_url
from opsdeck.infrastructure.github.client import PER_PAGE, GithubClient
from opsdeck.infrastructure.github.parsing import events_from_search_pull, parse_event
from opsdeck.infrastructure.github.search import search_queries, search_stamp
from opsdeck.infrastructure.github.token import FINE_GRAINED_HINT, token_kind

# The events API never serves more than 300 events per source (its last page
# then has no "next" link even though older history exists). Pages may hold
# 99 or 98 items, so "reached the cap" is judged with a little slack.
EVENTS_API_CAP = 300
EVENTS_CAP_SLACK = 15
REPO_FETCH_WORKERS = 8
# a remembered event window is read in full again after this long, in case
# GitHub reordered or dropped events below the pages a refresh looks at
EVENTS_RESYNC_SECONDS = 900.0
GRAPHQL_CHUNK = 40

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
          nodes { databaseId state submittedAt url body author { __typename login avatarUrl } }
        }
      }
    }
  }
}
""".replace("PAGE_SIZE", str(SEARCH_PAGE_SIZE))  # a placeholder keeps the GraphQL braces unescaped


@dataclass(frozen=True)
class _EventWindow:
    items: list[dict]  # raw events in API order
    more: bool  # older events remained beyond the pages read
    resync_at: float  # clock time after which the window is read in full again


class ActivityReader:
    def __init__(
        self,
        client: GithubClient,
        clock: Callable[[], float],
        org_pages: int,
        repo_pages: int,
        pr_cache_seconds: float,
        search_cache_seconds: float,
        events_resync_seconds: float,
    ):
        self._client = client
        self._clock = clock
        self._org_pages = org_pages
        self._repo_pages = repo_pages
        self._pr_cache_seconds = pr_cache_seconds
        self._search_cache_seconds = search_cache_seconds
        self._events_resync_seconds = events_resync_seconds
        self._lock = threading.Lock()
        self._windows: dict[str, _EventWindow] = {}
        self._search_cache: dict[str, tuple[float, list[dict]]] = {}
        self._pr_cache: dict[tuple[str, int], tuple[float, PullRequestInfo]] = {}

    def activity(self, spec: FeedSpec, realtime_since: datetime) -> ActivitySnapshot:
        viewer = self._client.viewer()
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
            quota=self._client.quota(),
        )

    # ----- events API ---------------------------------------------------

    def org_events(self, org: str) -> GithubEventBatch:
        return self._org_batch(org)[0]

    def repo_events(self, org: str, repo: str) -> GithubEventBatch:
        return self._event_batch(f"/repos/{org}/{repo}/events", self._repo_pages)

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
                    if self._fine_grained()
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

    def _org_batch(self, org: str) -> tuple[GithubEventBatch, bool]:
        """The org "dashboard" of the viewer, which includes private repos, or,
        when the token cannot read it, the org's public events (flagged True)."""
        try:
            path = f"/users/{self._client.viewer()}/events/orgs/{org}"
            return self._event_batch(path, self._org_pages), False
        except GithubNotFoundError:
            return self._event_batch(f"/orgs/{org}/events", self._org_pages), True

    def _public_only_warning(self, org: str) -> str:
        if self._fine_grained():
            hint = FINE_GRAINED_HINT
        else:
            hint = (
                f"Check that you are a member of {org} and that the token has the `repo`"
                f" and `read:org` scopes; if {org} uses SAML single sign-on, authorize"
                " the token for it."
            )
        return f"Only public events of {org} are shown: the token cannot read its private activity. {hint}"

    def _fine_grained(self) -> bool:
        return token_kind(self._client.token(raise_if_missing=False)) is TokenKind.FINE_GRAINED

    def _event_batch(self, path: str, max_pages: int) -> GithubEventBatch:
        raw, more_pages = self._event_window(path, max_pages)
        at_api_cap = len(raw) >= EVENTS_API_CAP - EVENTS_CAP_SLACK
        return GithubEventBatch([parse_event(item) for item in raw], more_pages or at_api_cap)

    def _event_window(self, path: str, max_pages: int) -> tuple[list[dict], bool]:
        """A source's raw events in API order (newest processed first), and
        whether older ones remained beyond the pages read.

        A remembered window is refreshed by walking the pages only until an
        event it already holds shows up: the new ones go on top and what
        falls past the page limit is dropped. Without a window (or once it is
        due for a resync) every page is read, which is also what the walk
        amounts to when no known event shows up."""
        window = self._windows.get(path)
        if window is None or window.resync_at <= self._clock():
            window = _EventWindow([], False, self._clock() + self._events_resync_seconds)
        known = {str(item.get("id")) for item in window.items}
        fresh: list[dict] = []
        more = False
        overlap = None
        for page in range(1, max_pages + 1):
            batch, more = self._client.get_with_next(path, per_page=PER_PAGE, page=page)
            if not isinstance(batch, list):
                more = False
                break
            overlap = next((i for i, item in enumerate(batch) if str(item.get("id")) in known), None)
            if overlap is not None:
                merged = fresh + batch[:overlap] + window.items
                limit = max_pages * PER_PAGE
                window = _EventWindow(merged[:limit], window.more or len(merged) > limit, window.resync_at)
                break
            fresh += batch
            if not more:
                break
        if overlap is None:
            window = _EventWindow(fresh, more, window.resync_at)
        with self._lock:
            self._windows[path] = window
        return window.items, window.more

    # ----- real-time PR search (GraphQL) ----------------------------------

    def recent_pull_request_events(self, org: str, repos: list[str], since: datetime) -> list[GithubEvent]:
        # whole-minute "since" keeps the search query (and its cache key)
        # stable across refreshes within the same minute
        floor = since.astimezone(UTC).replace(second=0, microsecond=0)
        events: list[GithubEvent] = []
        seen: set[str] = set()
        for query in search_queries(org, repos, search_stamp(floor)):
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
            body = self._client.graphql(SEARCH_QUERY, {"q": query, "after": after})
            search = body.get("search") or {}
            pulls.extend(node for node in search.get("nodes") or [] if node)
            page = search.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
        with self._lock:
            self._search_cache[query] = (self._clock() + self._search_cache_seconds, pulls)
        return pulls

    # ----- PR titles and states (GraphQL) ---------------------------------

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
        data = self._client.graphql("query { " + " ".join(parts) + " }", {})
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
