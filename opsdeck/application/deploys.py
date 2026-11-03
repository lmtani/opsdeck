"""Use cases of the deploys context: the deploys to draw over a query's results."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from opsdeck.domain.deploys import Deploy, DeployFeed, DeployTarget, DeployWindow
from opsdeck.domain.errors import NotFoundError
from opsdeck.domain.logs import LogSourceKind, LogSourceRepository


class DeployService:
    """Reads the deploys through the feed of the source's kind; a kind without
    one has no deploys to show."""

    def __init__(self, feeds: Mapping[LogSourceKind, DeployFeed], sources: LogSourceRepository):
        self._feeds = feeds
        self._sources = sources

    def deploys(
        self, source_id: str, targets: Sequence[DeployTarget], start: datetime, end: datetime
    ) -> list[Deploy]:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError(f"Log source '{source_id}' not found")
        window = DeployWindow(tuple(dict.fromkeys(targets)), start, end)
        feed = self._feeds.get(source.kind)
        if feed is None or not window.targets:
            return []
        return feed.deploys(source, window)
