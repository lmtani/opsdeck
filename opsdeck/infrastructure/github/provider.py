"""GithubProvider over GitHub's REST events API, the search API and GraphQL.

The port's methods are answered by one reader per view, all sharing one
GithubClient (token, conditional GETs, rate limit, viewer):

- activity.py       the timeline: event windows, real-time PR search, PR titles
- contributions.py  the contributors and network views
- context.py        the side panel with an event's full context
- repos.py          an org's repositories
- token.py          the token's kind, scopes and the instructions to fix it
"""

import time
from collections.abc import Callable
from datetime import datetime

import httpx

from opsdeck.domain.github import (
    ActivitySnapshot,
    ContributionsSnapshot,
    EventContext,
    EventSubject,
    FeedSpec,
    GithubEvent,
    GithubEventBatch,
    GithubProvider,
    GithubRepo,
    PullRequestInfo,
    Quota,
    TokenStatus,
)
from opsdeck.infrastructure.github import context, repos, token
from opsdeck.infrastructure.github.activity import EVENTS_RESYNC_SECONDS, ActivityReader
from opsdeck.infrastructure.github.client import API_URL, GithubClient
from opsdeck.infrastructure.github.contributions import ContributionsReader


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
        contributions_cache_seconds: float = 600.0,
        events_resync_seconds: float = EVENTS_RESYNC_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = GithubClient(token_source, transport, base_url, timeout_seconds)
        self._activity = ActivityReader(
            self._client,
            clock,
            org_pages=org_pages,
            repo_pages=repo_pages,
            pr_cache_seconds=pr_cache_seconds,
            search_cache_seconds=search_cache_seconds,
            events_resync_seconds=events_resync_seconds,
        )
        self._contributions = ContributionsReader(self._client, clock, contributions_cache_seconds)

    def is_available(self) -> bool:
        return bool(self._client.token(raise_if_missing=False))

    def token_status(self) -> TokenStatus:
        return token.token_status(self._client)

    def viewer(self) -> str:
        return self._client.viewer()

    def quota(self) -> Quota | None:
        return self._client.quota()

    def activity(self, spec: FeedSpec, realtime_since: datetime) -> ActivitySnapshot:
        return self._activity.activity(spec, realtime_since)

    def org_events(self, org: str) -> GithubEventBatch:
        return self._activity.org_events(org)

    def repo_events(self, org: str, repo: str) -> GithubEventBatch:
        return self._activity.repo_events(org, repo)

    def recent_pull_request_events(self, org: str, repos: list[str], since: datetime) -> list[GithubEvent]:
        return self._activity.recent_pull_request_events(org, repos, since)

    def pull_requests(self, refs: list[tuple[str, int]]) -> dict[tuple[str, int], PullRequestInfo]:
        return self._activity.pull_requests(refs)

    def contributions(self, spec: FeedSpec, since: datetime) -> ContributionsSnapshot:
        return self._contributions.contributions(spec, since)

    def org_repos(self, org: str) -> list[GithubRepo]:
        return repos.org_repos(self._client, org)

    def event_context(self, subject: EventSubject) -> EventContext:
        return context.event_context(self._client, subject)
