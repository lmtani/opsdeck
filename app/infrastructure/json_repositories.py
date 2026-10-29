"""JSON-file implementations of the repository interfaces.

Legacy/fallback backend (DASHBOARD_STORAGE=json). Implements the same
per-context interfaces as the MySQL repositories.
"""

from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path

from app.domain.github import GithubFeed, GithubFeedRepository
from app.domain.links import Link, LinkRepository
from app.domain.logs import LogFilter, LogFilterRepository, LogSource, LogSourceKind, LogSourceRepository
from app.domain.settings import AppSettings, SettingsRepository
from app.infrastructure.json_store import JsonStore
from app.infrastructure.rows import (
    feed_to_row,
    filter_to_row,
    link_to_row,
    row_to_feed,
    row_to_filter,
    row_to_link,
    row_to_source,
    source_to_row,
    values_to_settings,
)


def _known_fields(entity_class, data: dict) -> dict:
    known = {f.name for f in fields(entity_class)}
    return {k: v for k, v in data.items() if k in known}


class _JsonCollection:
    """Shared list-of-records logic for entity collections."""

    def __init__(self, path: Path, entity_class, to_row: Callable, from_row: Callable):
        self._store = JsonStore(path)
        self._entity_class = entity_class
        self._to_row = to_row
        self._from_row = from_row

    def _load(self, data: dict):
        return self._from_row(_known_fields(self._entity_class, data))

    def list_all(self) -> list:
        return [self._load(item) for item in self._store.read([])]

    def get(self, entity_id: str):
        for item in self._store.read([]):
            if item.get("id") == entity_id:
                return self._load(item)
        return None

    def save(self, entity) -> None:
        records = self._store.read([])
        record = self._to_row(entity)
        for index, item in enumerate(records):
            if item.get("id") == entity.id:
                records[index] = record
                break
        else:
            records.append(record)
        self._store.write(records)

    def delete(self, entity_id: str) -> bool:
        records = self._store.read([])
        remaining = [item for item in records if item.get("id") != entity_id]
        if len(remaining) == len(records):
            return False
        self._store.write(remaining)
        return True


class JsonLinkRepository(LinkRepository):
    def __init__(self, path: Path):
        self._collection = _JsonCollection(path, Link, link_to_row, row_to_link)

    def list_all(self) -> list[Link]:
        return self._collection.list_all()

    def get(self, link_id: str) -> Link | None:
        return self._collection.get(link_id)

    def save(self, link: Link) -> None:
        self._collection.save(link)

    def delete(self, link_id: str) -> bool:
        return self._collection.delete(link_id)


class JsonLogFilterRepository(LogFilterRepository):
    def __init__(self, path: Path):
        self._collection = _JsonCollection(path, LogFilter, filter_to_row, row_to_filter)

    def list_all(self) -> list[LogFilter]:
        return self._collection.list_all()

    def get(self, filter_id: str) -> LogFilter | None:
        return self._collection.get(filter_id)

    def save(self, log_filter: LogFilter) -> None:
        self._collection.save(log_filter)

    def delete(self, filter_id: str) -> bool:
        return self._collection.delete(filter_id)


class JsonLogSourceRepository(LogSourceRepository):
    def __init__(self, path: Path):
        self._collection = _JsonCollection(path, LogSource, source_to_row, row_to_source)

    def list_all(self) -> list[LogSource]:
        return self._collection.list_all()

    def get(self, source_id: str) -> LogSource | None:
        return self._collection.get(source_id)

    def save(self, source: LogSource) -> None:
        self._collection.save(source)

    def delete(self, source_id: str) -> bool:
        return self._collection.delete(source_id)


def adopt_legacy_projects(filters_path: Path, sources: LogSourceRepository) -> int:
    """Filters saved before log sources existed name a GCP project instead of
    a source: give each such project a gcp source (reusing one that reads the
    same project) and point the filters at it. Returns how many changed."""
    store = JsonStore(filters_path)
    records = store.read([])
    legacy = [record for record in records if record.get("project") and not record.get("source_id")]
    if not legacy:
        return 0
    by_project = {
        source.config["project"]: source.id
        for source in sources.list_all()
        if source.kind is LogSourceKind.GCP
    }
    for record in legacy:
        project = record.pop("project").strip()
        if project not in by_project:
            source = LogSource.new(name=project, kind=LogSourceKind.GCP, config={"project": project})
            sources.save(source)
            by_project[project] = source.id
        record["source_id"] = by_project[project]
    store.write(records)
    return len(legacy)


class JsonGithubFeedRepository(GithubFeedRepository):
    def __init__(self, path: Path):
        self._collection = _JsonCollection(path, GithubFeed, feed_to_row, row_to_feed)

    def list_all(self) -> list[GithubFeed]:
        return self._collection.list_all()

    def get(self, feed_id: str) -> GithubFeed | None:
        return self._collection.get(feed_id)

    def save(self, feed: GithubFeed) -> None:
        self._collection.save(feed)

    def delete(self, feed_id: str) -> bool:
        return self._collection.delete(feed_id)


class JsonSettingsRepository(SettingsRepository):
    def __init__(self, path: Path):
        self._store = JsonStore(path)

    def load(self) -> AppSettings:
        return values_to_settings(self._store.read({}))

    def save(self, settings: AppSettings) -> None:
        self._store.write(asdict(settings))
