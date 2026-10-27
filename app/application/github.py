"""Use cases of the GitHub context: saved feeds and the activity timeline.

The rules (burst folding, coverage, real-time merge) are pure functions in
app.domain.github_rules; this module orchestrates them over what the
provider read for a feed spec.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.application.common import apply_order
from app.domain.common import utc_now
from app.domain.errors import GithubError, NotFoundError
from app.domain.github import (
    FeedSpec,
    GithubActivity,
    GithubEvent,
    GithubFeed,
    GithubFeedRepository,
    GithubProvider,
    GithubRepo,
    TokenStatus,
    validate_org,
)
from app.domain.github_rules import (
    collapse_bursts,
    coverage_start,
    is_unseen,
    keep_event,
    merge_realtime,
)
from app.domain.ordering import next_position, ordered

MAX_FEED_EVENTS = 500
# Published events can lag by hours; PR activity of this recent window is
# also asked from the provider's near-real-time source.
REALTIME_WINDOW = timedelta(hours=12)


class GithubFeedService:
    def __init__(self, repository: GithubFeedRepository):
        self._repository = repository

    def list_feeds(self) -> list[GithubFeed]:
        return ordered(self._repository.list_all())

    def reorder_feeds(self, ids: list[str]) -> None:
        apply_order(self._repository, ids)

    def create_feed(self, **fields) -> GithubFeed:
        feed = GithubFeed.new(position=next_position(self._repository.list_all()), **fields)
        self._repository.save(feed)
        return feed

    def update_feed(self, feed_id: str, **fields) -> GithubFeed:
        existing = self._get(feed_id)
        existing.update(**fields)
        self._repository.save(existing)
        return existing

    def delete_feed(self, feed_id: str) -> None:
        if not self._repository.delete(feed_id):
            raise NotFoundError(f"Feed '{feed_id}' not found")

    def mark_seen(self, feed_id: str, seen_at: datetime | None = None) -> GithubFeed:
        feed = self._get(feed_id)
        if feed.mark_seen(seen_at):
            self._repository.save(feed)
        return feed

    def _get(self, feed_id: str) -> GithubFeed:
        feed = self._repository.get(feed_id)
        if feed is None:
            raise NotFoundError(f"Feed '{feed_id}' not found")
        return feed


class GithubActivityService:
    """Reads a feed's events from GitHub and shapes them for the timeline."""

    def __init__(self, provider: GithubProvider, feeds: GithubFeedRepository):
        self._provider = provider
        self._feeds = feeds

    def provider_available(self) -> bool:
        return self._provider.is_available()

    def token_status(self) -> TokenStatus:
        return self._provider.token_status()

    def run_feed(self, feed_id: str) -> GithubActivity:
        feed = self._feeds.get(feed_id)
        if feed is None:
            raise NotFoundError(f"Feed '{feed_id}' not found")
        return self._run_saved(feed)

    def _run_saved(self, feed: GithubFeed, enrich: bool = True) -> GithubActivity:
        """A saved feed's activity, with each event flagged against its mark."""
        activity = self.run_query(feed.spec, enrich=enrich)
        for event in activity.events:
            event.is_new = is_unseen(event, feed.last_seen_at)
        activity.unseen = sum(event.is_new for event in activity.events)
        return activity

    def run_query(self, spec: FeedSpec, enrich: bool = True) -> GithubActivity:
        snapshot = self._provider.activity(spec, realtime_since=utc_now() - REALTIME_WINDOW)
        warnings = list(snapshot.warnings)
        events = [event for batch in snapshot.batches for event in batch.events]
        events += merge_realtime(events, snapshot.realtime)
        unique = {event.id: event for event in events}.values()
        ordered_events = sorted(unique, key=lambda e: (e.created_at, e.id), reverse=True)
        kept = [event for event in ordered_events if keep_event(event, spec, snapshot.viewer)]
        kept = kept[:MAX_FEED_EVENTS]
        if enrich:
            self._enrich_pull_requests(kept, warnings)
        return GithubActivity(
            events=collapse_bursts(kept),
            viewer=snapshot.viewer,
            fetched_at=utc_now(),
            warnings=warnings,
            coverage_start=coverage_start(snapshot.batches),
            quota=snapshot.quota,
        )

    def unseen_counts(self) -> dict[str, int]:
        """Events newer than each feed's mark; feeds that fail are left out."""
        feeds = self._feeds.list_all()
        if not feeds:
            return {}

        def count(feed: GithubFeed) -> tuple[str, int | None]:
            try:
                return feed.id, self._run_saved(feed, enrich=False).unseen
            except GithubError:
                return feed.id, None

        with ThreadPoolExecutor(max_workers=min(4, len(feeds))) as pool:
            results = list(pool.map(count, feeds))
        return {feed_id: total for feed_id, total in results if total is not None}

    def list_org_repos(self, org: str) -> list[GithubRepo]:
        return self._provider.org_repos(validate_org(org))

    # ----- helpers -----------------------------------------------------

    def _enrich_pull_requests(self, events: list[GithubEvent], warnings: list[str]) -> None:
        """Fill PR title/current state, which the events API no longer sends."""
        refs = [(e.repo, e.number) for e in events if e.number and e.is_pull]
        if not refs:
            return
        try:
            infos = self._provider.pull_requests(refs)
        except GithubError as error:
            warnings.append(f"PR titles unavailable: {error}")
            return
        for event in events:
            info = infos.get((event.repo, event.number)) if event.is_pull else None
            if info is None:
                continue
            event.title = event.title or info.title
            event.state = info.state
