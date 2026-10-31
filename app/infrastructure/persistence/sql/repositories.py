"""SQL repositories (SQLite or MySQL through SQLAlchemy Core): one short
transaction per call, rows mapped to entities by rows.py."""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy import Engine, Table, delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.domain.github import GithubFeed, GithubFeedRepository
from app.domain.links import Link, LinkRepository
from app.domain.logs import LogFilter, LogFilterRepository, LogSource, LogSourceRepository
from app.domain.settings import AppSettings, SettingsRepository
from app.infrastructure.persistence.rows import (
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
from app.infrastructure.persistence.sql import tables

# columns an upsert never rewrites
_KEPT = {"created_at"}


def _upsert(connection, table: Table, row: dict, key: str) -> None:
    """Insert the row, or update every column but the key and created_at."""
    changed = [name for name in row if name != key and name not in _KEPT]
    if connection.dialect.name == "mysql":
        on_mysql = mysql_insert(table).values(row)
        connection.execute(
            on_mysql.on_duplicate_key_update({name: on_mysql.inserted[name] for name in changed})
        )
    else:
        on_sqlite = sqlite_insert(table).values(row)
        connection.execute(
            on_sqlite.on_conflict_do_update(
                index_elements=[key], set_={name: on_sqlite.excluded[name] for name in changed}
            )
        )


class _TableRepository[T]:
    """list_all/get/save/delete of one entity over one table keyed by id."""

    def __init__(
        self,
        engine: Engine,
        table: Table,
        to_row: Callable[[T], dict[str, Any]],
        from_row: Callable[[dict[str, Any]], T],
    ):
        self._engine = engine
        self._table = table
        self._to_row = to_row
        self._from_row = from_row

    def list_all(self) -> list[T]:
        query = select(self._table).order_by(self._table.c.created_at, self._table.c.id)
        with self._engine.connect() as connection:
            return [self._from_row(dict(row._mapping)) for row in connection.execute(query)]

    def get(self, entity_id: str) -> T | None:
        query = select(self._table).where(self._table.c.id == entity_id)
        with self._engine.connect() as connection:
            row = connection.execute(query).first()
        return self._from_row(dict(row._mapping)) if row else None

    def save(self, entity: T) -> None:
        with self._engine.begin() as connection:
            _upsert(connection, self._table, self._to_row(entity), "id")

    def delete(self, entity_id: str) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(delete(self._table).where(self._table.c.id == entity_id))
        return result.rowcount > 0


class SqlLinkRepository(_TableRepository[Link], LinkRepository):
    def __init__(self, engine: Engine):
        super().__init__(engine, tables.links, link_to_row, row_to_link)


class SqlLogFilterRepository(_TableRepository[LogFilter], LogFilterRepository):
    def __init__(self, engine: Engine):
        super().__init__(engine, tables.log_filters, filter_to_row, row_to_filter)


def _source_to_sql(source: LogSource) -> dict:
    row = source_to_row(source)
    row["config"] = json.dumps(row["config"], ensure_ascii=False, sort_keys=True)
    return row


def _source_from_sql(row: dict) -> LogSource:
    return row_to_source({**row, "config": json.loads(row["config"] or "{}")})


class SqlLogSourceRepository(_TableRepository[LogSource], LogSourceRepository):
    """config is a JSON object in a text column."""

    def __init__(self, engine: Engine):
        super().__init__(engine, tables.log_sources, _source_to_sql, _source_from_sql)


def _split(text: str | None) -> list[str]:
    return [item for item in (text or "").split(",") if item]


def _feed_to_sql(feed: GithubFeed) -> dict:
    row = feed_to_row(feed)
    row["repos"] = ",".join(row["repos"])
    row["event_kinds"] = ",".join(row["event_kinds"])
    return row


def _feed_from_sql(row: dict) -> GithubFeed:
    return row_to_feed({**row, "repos": _split(row["repos"]), "event_kinds": _split(row["event_kinds"])})


class SqlGithubFeedRepository(_TableRepository[GithubFeed], GithubFeedRepository):
    """repos and event_kinds are comma-separated text."""

    def __init__(self, engine: Engine):
        super().__init__(engine, tables.github_feeds, _feed_to_sql, _feed_from_sql)


class SqlSettingsRepository(SettingsRepository):
    """Settings stored as name/value text rows, so new preferences need no migration."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def load(self) -> AppSettings:
        with self._engine.connect() as connection:
            stored = {row.name: row.value for row in connection.execute(select(tables.settings))}
        return values_to_settings(stored)

    def save(self, settings: AppSettings) -> None:
        with self._engine.begin() as connection:
            for name, value in asdict(settings).items():
                _upsert(connection, tables.settings, {"name": name, "value": str(value)}, "name")
