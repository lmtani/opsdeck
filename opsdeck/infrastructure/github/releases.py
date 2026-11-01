"""ReleaseFeed over GitHub Releases: the repository's latest release (drafts
and pre-releases excluded by GitHub). Anonymous on purpose: the repository
is public, a check runs a few times a day, and the user's token may be one
that only works for their organization."""

import httpx

from opsdeck.domain.common import parse_timestamp
from opsdeck.domain.errors import ExternalServiceError
from opsdeck.domain.updates import Release, ReleaseFeed, parse_version

API = "https://api.github.com"


class GithubReleaseFeed(ReleaseFeed):
    def __init__(self, repo: str, transport: httpx.BaseTransport | None = None, timeout: float = 10):
        self._repo = repo
        self._transport = transport
        self._timeout = timeout

    def latest(self) -> Release | None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.get(f"{API}/repos/{self._repo}/releases/latest", headers=headers)
        except httpx.HTTPError as error:
            raise ExternalServiceError(f"Could not read the releases of {self._repo}: {error}") from None
        if response.status_code == 404:
            return None  # no release yet
        if response.status_code != 200:
            raise ExternalServiceError(f"GitHub answered {response.status_code} for the latest release")
        try:
            body = response.json()
            tag, url = body["tag_name"], body["html_url"]
        except (ValueError, KeyError, TypeError):
            raise ExternalServiceError("GitHub sent an unexpected latest release") from None
        if not isinstance(tag, str) or parse_version(tag) is None or not isinstance(url, str):
            return None  # a tag that is not a version names no release to offer
        published = body.get("published_at")
        return Release(
            version=tag.removeprefix("v"),
            url=url if url.startswith("https://github.com/") else f"https://github.com/{self._repo}/releases",
            published_at=parse_timestamp(published) if isinstance(published, str) else None,
        )
