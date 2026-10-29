"""Use cases of the logs context: log sources, saved filters and log queries."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.application.common import apply_order
from app.domain.errors import NotFoundError, ValidationError
from app.domain.logs import (
    LogEntry,
    LogFilter,
    LogFilterRepository,
    LogProvider,
    LogQuery,
    LogSource,
    LogSourceKind,
    LogSourceRepository,
    Refinement,
)
from app.domain.ordering import next_position, ordered


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

    def run_saved_filter(self, filter_id: str, refinements: Sequence[Refinement] = ()) -> LogResult:
        log_filter = self._filters.get(filter_id)
        if log_filter is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        return self.run_query(log_filter.source_id, log_filter.query().refined(refinements))

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
        provider = self._providers.get(kind)
        if provider is None:  # a kind the domain knows but this build does not wire
            raise ValidationError(f"Log sources of kind '{kind}' are not supported here")
        return provider
