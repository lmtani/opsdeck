"""Use case of the update notice: the running version against the newest
release, read at most once per `check_every` (a failed read is retried
sooner) so the notice costs a few requests a day."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from opsdeck.domain.common import utc_now
from opsdeck.domain.errors import ExternalServiceError
from opsdeck.domain.updates import Release, ReleaseFeed

CHECK_EVERY = timedelta(hours=6)
RETRY_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class UpdateStatus:
    current: str
    latest: Release | None  # None: not checked (turned off), unreadable or no release yet
    newer: bool  # latest is a newer release than current
    service: bool = False  # this install runs as a service: an update needs `opsdeck service restart`


class UpdateService:
    def __init__(
        self,
        feed: ReleaseFeed,
        current: str,
        enabled: bool = True,
        clock: Callable[[], datetime] = utc_now,
        service: bool = False,
    ):
        self._feed = feed
        self._current = current
        self._enabled = enabled
        self._clock = clock
        self._service = service
        self._latest: Release | None = None
        self._next_check: datetime | None = None

    @property
    def current(self) -> str:
        return self._current

    def status(self) -> UpdateStatus:
        if self._enabled and (self._next_check is None or self._clock() >= self._next_check):
            try:
                self._latest = self._feed.latest()
                self._next_check = self._clock() + CHECK_EVERY
            except ExternalServiceError:
                self._next_check = self._clock() + RETRY_AFTER  # keep what the last read said
        latest = self._latest
        return UpdateStatus(
            current=self._current,
            latest=latest,
            newer=latest is not None and latest.is_newer_than(self._current),
            service=self._service,
        )
