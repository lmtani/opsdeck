"""Use cases of the logs context: log sources, saved filters, log queries and
the background count of new entries of watched filters."""

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.application.common import apply_order
from app.domain.common import utc_now
from app.domain.errors import LogProviderError, NotFoundError, ValidationError
from app.domain.logs import (
    LogCount,
    LogEntry,
    LogFilter,
    LogFilterRepository,
    LogProvider,
    LogQuery,
    LogSource,
    LogSourceKind,
    LogSourceRepository,
    Refinement,
    SeverityBand,
    TimeRange,
)
from app.domain.ordering import next_position, ordered


def _provider_for(providers: Mapping[LogSourceKind, LogProvider], kind: LogSourceKind) -> LogProvider:
    provider = providers.get(kind)
    if provider is None:  # a kind the domain knows but this build does not wire
        raise ValidationError(f"Log sources of kind '{kind}' are not supported here")
    return provider


def _by_name(sources: list[LogSource]) -> list[LogSource]:
    return sorted(sources, key=lambda source: (source.name.casefold(), source.created_at, source.id))


class LogSourceService:
    """Sources are listed by name. Names are unique (case-insensitive), so a
    filter's source reads unambiguously in the sidebar and in imports."""

    def __init__(self, sources: LogSourceRepository, filters: LogFilterRepository):
        self._sources = sources
        self._filters = filters

    def list_sources(self) -> list[LogSource]:
        return _by_name(self._sources.list_all())

    def create_source(self, name: str, kind: LogSourceKind | str, config: Mapping[str, str]) -> LogSource:
        source = LogSource.new(name=name, kind=kind, config=config)
        self._check_name_free(source)
        self._sources.save(source)
        return source

    def update_source(self, source_id: str, name: str, config: Mapping[str, str]) -> LogSource:
        source = replace(self._get(source_id))  # a copy: nothing changes unless the name is free
        source.update(name=name, config=config)
        self._check_name_free(source)
        self._sources.save(source)
        return source

    def delete_source(self, source_id: str) -> None:
        source = self._get(source_id)
        used = [item for item in self._filters.list_all() if item.source_id == source.id]
        if used:
            count = f"{len(used)} filter{'s' if len(used) > 1 else ''}"
            raise ValidationError(f"{count} still use '{source.name}'; move or remove them first")
        self._sources.delete(source.id)

    def _get(self, source_id: str) -> LogSource:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError(f"Log source '{source_id}' not found")
        return source

    def _check_name_free(self, source: LogSource) -> None:
        wanted = source.name.casefold()
        others = (other for other in self._sources.list_all() if other.id != source.id)
        if any(other.name.casefold() == wanted for other in others):
            raise ValidationError(f"There is already a log source named '{source.name}'")


class LogFilterService:
    def __init__(self, repository: LogFilterRepository, sources: LogSourceRepository):
        self._repository = repository
        self._sources = sources

    def list_filters(self) -> list[LogFilter]:
        return ordered(self._repository.list_all())

    def reorder_filters(self, ids: list[str]) -> None:
        apply_order(self._repository, ids)

    def create_filter(self, **fields) -> LogFilter:
        log_filter = LogFilter.new(position=next_position(self._repository.list_all()), **fields)
        self._check_source(log_filter)
        self._repository.save(log_filter)
        return log_filter

    def update_filter(self, filter_id: str, **fields) -> LogFilter:
        existing = self._repository.get(filter_id)
        if existing is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        updated = replace(existing)  # a copy: nothing changes unless the source exists
        updated.update(**fields)
        self._check_source(updated)
        self._repository.save(updated)
        return updated

    def delete_filter(self, filter_id: str) -> None:
        if not self._repository.delete(filter_id):
            raise NotFoundError(f"Filter '{filter_id}' not found")

    def _check_source(self, log_filter: LogFilter) -> None:
        if self._sources.get(log_filter.source_id) is None:
            raise ValidationError("The chosen log source does not exist")


@dataclass
class LogResult:
    """What a query ran (in the source's language) and what it returned."""

    source: LogSource
    expression: str  # the saved expression with the refinements ANDed on
    command: str  # the equivalent CLI command, for copy/paste
    console_url: str  # the query in the backend's web console, or ""
    entries: list[LogEntry]


class LogQueryService:
    def __init__(
        self,
        providers: Mapping[LogSourceKind, LogProvider],
        sources: LogSourceRepository,
        filters: LogFilterRepository,
    ):
        self._providers = providers
        self._sources = sources
        self._filters = filters

    def providers_available(self) -> bool | None:
        """Whether every kind of source in use can be queried; None without sources."""
        kinds = {source.kind for source in self._sources.list_all()}
        if not kinds:
            return None
        return all(self._provider(kind).is_available() for kind in kinds)

    def run_saved_filter(
        self,
        filter_id: str,
        refinements: Sequence[Refinement] = (),
        time_range: TimeRange | None = None,
        bands: Sequence[SeverityBand] = (),
    ) -> LogResult:
        """The saved query plus this run's refinements; a time range replaces
        its freshness and bands narrow it to those severities (the new
        entries a watched filter counted, which may be older than its window)."""
        log_filter = self._filters.get(filter_id)
        if log_filter is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        query = replace(log_filter.query().refined(refinements), time_range=time_range, bands=tuple(bands))
        return self.run_query(log_filter.source_id, query)

    def run_query(self, source_id: str, query: LogQuery) -> LogResult:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError(f"Log source '{source_id}' not found")
        provider = self._provider(source.kind)
        return LogResult(
            source=source,
            expression=provider.expression(query),
            command=provider.describe(source, query),
            console_url=provider.console_url(source, query),
            entries=provider.query(source, query),
        )

    def _provider(self, kind: LogSourceKind) -> LogProvider:
        return _provider_for(self._providers, kind)


@dataclass(frozen=True)
class WatchStatus:
    """What a watched filter caught since it was last opened: entries with a
    timestamp in [since, until), per severity band."""

    filter_id: str
    since: datetime
    until: datetime
    count: LogCount
    problem: str = ""  # why the last count failed; the previous numbers stay


@dataclass
class _Tally:
    mark: datetime  # the filter's last_seen_at this tally counts from
    until: datetime  # entries before this instant are counted
    count: LogCount = field(default_factory=LogCount)
    checked_at: datetime | None = None
    problem: str = ""


class LogWatchService:
    """Counts the new entries of watched filters, incrementally.

    Each round reads only the interval since the previous one, [until,
    now - lag), so a filter costs one provider read per round and the
    intervals never overlap. `lag` leaves time for late ingestion; a filter
    is read at most once per `min_interval` however often `unseen` is asked.
    Tallies live in memory: after a restart the first read spans the whole
    time since the filter was opened, up to `cap` entries.
    """

    def __init__(
        self,
        providers: Mapping[LogSourceKind, LogProvider],
        sources: LogSourceRepository,
        filters: LogFilterRepository,
        clock: Callable[[], datetime] = utc_now,
        min_interval: timedelta = timedelta(seconds=60),
        lag: timedelta = timedelta(seconds=60),
        cap: int = 1000,
        workers: int = 4,
    ):
        self._providers = providers
        self._sources = sources
        self._filters = filters
        self._clock = clock
        self._min_interval = min_interval
        self._lag = lag
        self._cap = cap
        self._workers = workers
        self._tallies: dict[str, _Tally] = {}
        self._lock = threading.Lock()

    def unseen(self) -> list[WatchStatus]:
        watched = [item for item in ordered(self._filters.list_all()) if item.watch]
        now = self._clock()
        with self._lock:  # two callers at once must not count an interval twice
            self._tallies = {item.id: self._tally(item, now) for item in watched}
            due = [item for item in watched if self._due(self._tallies[item.id], now)]
            if due:
                with ThreadPoolExecutor(max_workers=min(self._workers, len(due))) as pool:
                    list(pool.map(lambda item: self._advance(item, self._tallies[item.id], now), due))
            return [self._status(item.id) for item in watched]

    def set_watch(self, filter_id: str, watch: bool) -> LogFilter:
        log_filter = self._get(filter_id)
        log_filter.set_watch(watch, self._clock())
        self._filters.save(log_filter)
        return log_filter

    def mark_seen(self, filter_id: str, at: datetime | None = None) -> LogFilter:
        """The user saw the filter's entries up to `at` (never later than now,
        the default): what came before is no longer new."""
        log_filter = self._get(filter_id)
        now = self._clock()
        if log_filter.mark_seen(min(at, now) if at else now):
            self._filters.save(log_filter)
        return log_filter

    def _get(self, filter_id: str) -> LogFilter:
        log_filter = self._filters.get(filter_id)
        if log_filter is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        return log_filter

    def _tally(self, log_filter: LogFilter, now: datetime) -> _Tally:
        """The filter's running tally, or a fresh one when it was opened since."""
        mark = log_filter.last_seen_at or now
        tally = self._tallies.get(log_filter.id)
        if tally is not None and tally.mark == mark:
            return tally
        return _Tally(mark=mark, until=mark)

    def _status(self, filter_id: str) -> WatchStatus:
        tally = self._tallies[filter_id]
        return WatchStatus(filter_id, tally.mark, tally.until, tally.count, tally.problem)

    def _due(self, tally: _Tally, now: datetime) -> bool:
        return tally.checked_at is None or now - tally.checked_at >= self._min_interval

    def _advance(self, log_filter: LogFilter, tally: _Tally, now: datetime) -> None:
        tally.checked_at = now
        end = now - self._lag
        if end <= tally.until:
            return
        source = self._sources.get(log_filter.source_id)
        if source is None:
            tally.problem = "The filter's log source no longer exists"
            return
        try:
            provider = _provider_for(self._providers, source.kind)
            counted = provider.count(source, log_filter.query(), tally.until, end, self._cap)
        except (LogProviderError, ValidationError) as error:
            tally.problem = str(error)
            return
        tally.count += counted
        tally.until = end
        tally.problem = ""
