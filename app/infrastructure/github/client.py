"""The HTTP side of every GitHub read: one httpx client with the token, the
REST and GraphQL calls, pagination and the errors turned into instructions.

Every plain GET is conditional: the client remembers each URL's ETag and body,
and a 304 answer (which does not count against the rate limit) reuses the
remembered body. That keeps 60 s polling of several feeds cheap. Reads with
the html media type are never conditional (see `read_html`). The core rate
limit left after the last response is kept for `quota`, and the token's login
for `viewer`.
"""

import threading
import time
from collections.abc import Callable

import httpx

from app.domain.errors import GithubError, GithubNotFoundError
from app.domain.github import Quota
from app.infrastructure.github.token import CLASSIC_HINT, README_HINT

API_URL = "https://api.github.com"
# asks the REST API for bodies rendered as HTML (body_html) instead of Markdown
HTML_MEDIA_TYPE = "application/vnd.github.html+json"
PER_PAGE = 100


class GithubClient:
    def __init__(
        self,
        token_source: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_URL,
        timeout_seconds: float = 15.0,
    ):
        self._token_source = token_source
        self._token = ""
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout_seconds,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "opsdeck-dashboard",
            },
        )
        self._lock = threading.Lock()
        self._etags: dict[str, tuple[str, object, bool]] = {}
        self._viewer = ""
        self._rate: tuple[int, int] | None = None

    # ----- token and viewer ---------------------------------------------

    def token(self, raise_if_missing: bool = True) -> str:
        """The token, resolved from the source on first use (and again after a 401)."""
        if not self._token:
            self._token = self._token_source() or ""
        if not self._token and raise_if_missing:
            raise GithubError("No GitHub token: set GITHUB_TOKEN or run `gh auth login`")
        return self._token

    def viewer(self) -> str:
        """The login the token belongs to."""
        if not self._viewer:
            user = self.get("/user")
            self._viewer = user.get("login", "") if isinstance(user, dict) else ""
        return self._viewer

    def remember_viewer(self, login: str) -> None:
        self._viewer = self._viewer or login

    def quota(self) -> Quota | None:
        """What was left of the REST allowance as of the last response."""
        return Quota(*self._rate) if self._rate else None

    # ----- REST ---------------------------------------------------------

    def send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise GithubError(f"Could not reach GitHub: {error}") from None
        self._track_rate(response)
        if response.status_code == 401:
            self._token = ""  # e.g. rotated gh token: resolve again next time
            raise GithubError(
                "GitHub rejected the token: it is invalid, expired or revoked."
                f" Create a new one and update GITHUB_TOKEN. {README_HINT}"
            )
        if response.status_code in (403, 429) and _rate_exhausted(response):
            raise GithubError(_rate_message(response))
        if response.status_code == 404:
            raise GithubNotFoundError(f"Not found on GitHub: {path}")
        if response.status_code == 403:
            raise GithubError(_forbidden_message(_message(response)))
        if response.status_code >= 400:
            raise GithubError(f"GitHub answered {response.status_code}: {_message(response)}")
        return response

    def get(self, path: str, **params) -> object:
        return self.get_with_next(path, **params)[0]

    def get_with_next(self, path: str, **params) -> tuple[object, bool]:
        """Body plus whether the Link header announces a next page."""
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self._etags.get(key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        response = self.send("GET", path, params=params, headers=headers)
        if response.status_code == 304 and cached:
            return cached[1], cached[2]
        data = response.json()
        has_next = "next" in response.links
        etag = response.headers.get("etag")
        if etag:
            with self._lock:
                self._etags[key] = (etag, data, has_next)
        return data, has_next

    def paged(self, path: str, max_pages: int, **params) -> list[dict]:
        return self.paged_with_more(path, max_pages, **params)[0]

    def paged_with_more(self, path: str, max_pages: int, **params) -> tuple[list[dict], bool]:
        """Items of up to `max_pages` pages, and whether more pages remained.

        GitHub may return fewer than per_page items on a page that still has a
        successor, so this follows the Link header instead of the page size.
        """
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            batch, has_next = self.get_with_next(path, per_page=PER_PAGE, page=page, **params)
            if not isinstance(batch, list):
                return items, False
            items.extend(batch)
            if not has_next:
                return items, False
        return items, True

    # Reads with the html media type are not conditional: the signed URLs of
    # private images in body_html expire within minutes, so a remembered body
    # would show broken images.
    def read_html(self, path: str) -> dict:
        data = self.send("GET", path, headers={"Accept": HTML_MEDIA_TYPE}).json()
        if not isinstance(data, dict):
            raise GithubError("GitHub returned an unexpected answer for this item")
        return data

    def read_html_pages(self, path: str, max_pages: int, **params) -> list[dict]:
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            response = self.send(
                "GET",
                path,
                params={**params, "per_page": PER_PAGE, "page": page},
                headers={"Accept": HTML_MEDIA_TYPE},
            )
            batch = response.json()
            if not isinstance(batch, list):
                break
            items.extend(item for item in batch if isinstance(item, dict))
            if "next" not in response.links:
                break
        return items

    # ----- GraphQL ------------------------------------------------------

    def graphql(self, query: str, variables: dict) -> dict:
        response = self.send("POST", "/graphql", json={"query": query, "variables": variables})
        body = response.json()
        data = body.get("data")
        if data is None:
            errors = "; ".join(e.get("message", "") for e in body.get("errors", []))
            raise GithubError(f"GitHub GraphQL failed: {errors or 'no data'}")
        return data

    def _track_rate(self, response: httpx.Response) -> None:
        resource = response.headers.get("x-ratelimit-resource", "core")
        remaining = response.headers.get("x-ratelimit-remaining")
        limit = response.headers.get("x-ratelimit-limit")
        if resource == "core" and remaining is not None and limit is not None:
            self._rate = (int(remaining), int(limit))


def _rate_exhausted(response: httpx.Response) -> bool:
    return (
        response.status_code == 429
        or response.headers.get("x-ratelimit-remaining") == "0"
        or "rate limit" in _message(response).lower()
    )


def _rate_message(response: httpx.Response) -> str:
    reset = response.headers.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        minutes = max(1, round((int(reset) - time.time()) / 60))
        return f"GitHub rate limit exhausted; resets in ~{minutes} min"
    return "GitHub rate limit exhausted; try again in a few minutes"


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
