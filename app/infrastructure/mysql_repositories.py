"""MySQL implementations of the repository interfaces."""

from dataclasses import asdict

from app.domain.github import GithubFeed, GithubFeedRepository
from app.domain.links import Link, LinkRepository
from app.domain.logs import LogFilter, LogFilterRepository
from app.domain.settings import AppSettings, SettingsRepository
from app.infrastructure.mysql_connection import MySqlConnectionFactory
from app.infrastructure.rows import (
    feed_to_row,
    filter_to_row,
    link_to_row,
    row_to_feed,
    row_to_filter,
    row_to_link,
    values_to_settings,
)

GITHUB_FEED_COLUMNS = (
    "id, name, org, repos, event_kinds, hide_own, hide_bots, description,"
    " last_seen_at, position, created_at"
)


def _split(text: str | None) -> list[str]:
    return [item for item in (text or "").split(",") if item]


def _feed_from_sql(row: dict) -> GithubFeed:
    # repos and event_kinds are comma-separated text columns
    values = dict(row)
    values["repos"] = _split(values.get("repos"))
    values["event_kinds"] = _split(values.get("event_kinds"))
    return row_to_feed(values)


def _feed_to_sql(feed: GithubFeed) -> dict:
    row = feed_to_row(feed)
    row["repos"] = ",".join(row["repos"])
    row["event_kinds"] = ",".join(row["event_kinds"])
    return row


class MySqlLinkRepository(LinkRepository):
    _COLUMNS = (
        "id, title, url, kind, command, description, image_url, category, accent,"
        " position, created_at"
    )

    def __init__(self, factory: MySqlConnectionFactory):
        self._factory = factory

    def list_all(self) -> list[Link]:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._COLUMNS} FROM links ORDER BY created_at, id"
                )
                return [row_to_link(row) for row in cursor.fetchall()]

    def get(self, link_id: str) -> Link | None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._COLUMNS} FROM links WHERE id = %s", (link_id,)
                )
                row = cursor.fetchone()
                return row_to_link(row) if row else None

    def save(self, link: Link) -> None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO links"
                    " (id, title, url, kind, command, description, image_url,"
                    "  category, accent, position, created_at)"
                    " VALUES (%(id)s, %(title)s, %(url)s, %(kind)s, %(command)s,"
                    "         %(description)s, %(image_url)s, %(category)s,"
                    "         %(accent)s, %(position)s, %(created_at)s)"
                    " ON DUPLICATE KEY UPDATE"
                    "  title = VALUES(title), url = VALUES(url),"
                    "  kind = VALUES(kind), command = VALUES(command),"
                    "  description = VALUES(description), image_url = VALUES(image_url),"
                    "  category = VALUES(category), accent = VALUES(accent),"
                    "  position = VALUES(position)",
                    link_to_row(link),
                )

    def delete(self, link_id: str) -> bool:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM links WHERE id = %s", (link_id,))
                return cursor.rowcount > 0


class MySqlLogFilterRepository(LogFilterRepository):
    _COLUMNS = (
        "id, name, project, expression, freshness, `limit`,"
        " description, line_format, position, created_at"
    )

    def __init__(self, factory: MySqlConnectionFactory):
        self._factory = factory

    def list_all(self) -> list[LogFilter]:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._COLUMNS} FROM log_filters ORDER BY created_at, id"
                )
                return [row_to_filter(row) for row in cursor.fetchall()]

    def get(self, filter_id: str) -> LogFilter | None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._COLUMNS} FROM log_filters WHERE id = %s",
                    (filter_id,),
                )
                row = cursor.fetchone()
                return row_to_filter(row) if row else None

    def save(self, log_filter: LogFilter) -> None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO log_filters"
                    " (id, name, project, expression, freshness, `limit`,"
                    "  description, line_format, position, created_at)"
                    " VALUES (%(id)s, %(name)s, %(project)s, %(expression)s,"
                    "         %(freshness)s, %(limit)s, %(description)s,"
                    "         %(line_format)s, %(position)s, %(created_at)s)"
                    " ON DUPLICATE KEY UPDATE"
                    "  name = VALUES(name), project = VALUES(project),"
                    "  expression = VALUES(expression), freshness = VALUES(freshness),"
                    "  `limit` = VALUES(`limit`), description = VALUES(description),"
                    "  line_format = VALUES(line_format), position = VALUES(position)",
                    filter_to_row(log_filter),
                )

    def delete(self, filter_id: str) -> bool:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM log_filters WHERE id = %s", (filter_id,))
                return cursor.rowcount > 0


class MySqlSettingsRepository(SettingsRepository):
    """Settings stored as name/value rows so new preferences need no migration."""

    def __init__(self, factory: MySqlConnectionFactory):
        self._factory = factory

    def load(self) -> AppSettings:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name, value FROM settings")
                stored = {row["name"]: row["value"] for row in cursor.fetchall()}
        return values_to_settings(stored)

    def save(self, settings: AppSettings) -> None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                for name, value in asdict(settings).items():
                    cursor.execute(
                        "INSERT INTO settings (name, value) VALUES (%s, %s)"
                        " ON DUPLICATE KEY UPDATE value = VALUES(value)",
                        (name, str(value)),
                    )


class MySqlGithubFeedRepository(GithubFeedRepository):
    def __init__(self, factory: MySqlConnectionFactory):
        self._factory = factory

    def list_all(self) -> list[GithubFeed]:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {GITHUB_FEED_COLUMNS} FROM github_feeds ORDER BY created_at, id"
                )
                return [_feed_from_sql(row) for row in cursor.fetchall()]

    def get(self, feed_id: str) -> GithubFeed | None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {GITHUB_FEED_COLUMNS} FROM github_feeds WHERE id = %s",
                    (feed_id,),
                )
                row = cursor.fetchone()
                return _feed_from_sql(row) if row else None

    def save(self, feed: GithubFeed) -> None:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO github_feeds"
                    " (id, name, org, repos, event_kinds, hide_own, hide_bots,"
                    "  description, last_seen_at, position, created_at)"
                    " VALUES (%(id)s, %(name)s, %(org)s, %(repos)s, %(event_kinds)s,"
                    "         %(hide_own)s, %(hide_bots)s, %(description)s,"
                    "         %(last_seen_at)s, %(position)s, %(created_at)s)"
                    " ON DUPLICATE KEY UPDATE"
                    "  name = VALUES(name), org = VALUES(org), repos = VALUES(repos),"
                    "  event_kinds = VALUES(event_kinds), hide_own = VALUES(hide_own),"
                    "  hide_bots = VALUES(hide_bots), description = VALUES(description),"
                    "  last_seen_at = VALUES(last_seen_at), position = VALUES(position)",
                    _feed_to_sql(feed),
                )

    def delete(self, feed_id: str) -> bool:
        with self._factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM github_feeds WHERE id = %s", (feed_id,))
                return cursor.rowcount > 0
