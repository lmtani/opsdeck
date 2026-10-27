"""Use cases of the logs context: saved filters and log queries."""

from app.application.common import apply_order
from app.domain.errors import NotFoundError
from app.domain.logs import LogEntry, LogFilter, LogFilterRepository, LogProvider, LogQuery
from app.domain.ordering import next_position, ordered


class LogFilterService:
    def __init__(self, repository: LogFilterRepository):
        self._repository = repository

    def list_filters(self) -> list[LogFilter]:
        return ordered(self._repository.list_all())

    def reorder_filters(self, ids: list[str]) -> None:
        apply_order(self._repository, ids)

    def create_filter(self, **fields) -> LogFilter:
        log_filter = LogFilter.new(position=next_position(self._repository.list_all()), **fields)
        self._repository.save(log_filter)
        return log_filter

    def update_filter(self, filter_id: str, **fields) -> LogFilter:
        existing = self._repository.get(filter_id)
        if existing is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        existing.update(**fields)
        self._repository.save(existing)
        return existing

    def delete_filter(self, filter_id: str) -> None:
        if not self._repository.delete(filter_id):
            raise NotFoundError(f"Filter '{filter_id}' not found")


class LogQueryService:
    def __init__(self, provider: LogProvider, filters: LogFilterRepository):
        self._provider = provider
        self._filters = filters

    def provider_available(self) -> bool:
        return self._provider.is_available()

    def run_saved_filter(self, filter_id: str) -> tuple[str, list[LogEntry]]:
        log_filter = self._filters.get(filter_id)
        if log_filter is None:
            raise NotFoundError(f"Filter '{filter_id}' not found")
        return self.run_query(log_filter.query())

    def run_query(self, query: LogQuery) -> tuple[str, list[LogEntry]]:
        command = self._provider.describe(query)
        entries = self._provider.query(query)
        return command, entries
