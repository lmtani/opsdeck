"""The schema, declared once for every SQL backend (SQLite and MySQL 8).

Timestamps are ISO-8601 UTC text (rows.py), lists are comma-separated text
and a source's config is a JSON object in a text column, so the same values
read back alike everywhere. The migrations in migrations/versions/ build
this schema; a test checks that both still agree.
"""

from typing import Any

from sqlalchemy import CHAR, Boolean, Column, Integer, MetaData, String, Table, Text

metadata = MetaData()

# MySQL table options; other dialects ignore them
_MYSQL: dict[str, Any] = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _id() -> Column:
    return Column("id", CHAR(32), primary_key=True)


def _text(name: str, length: int, default: str = "") -> Column:
    return Column(name, String(length), nullable=False, server_default=default)


def _position() -> Column:
    return Column("position", Integer, nullable=False, server_default="0")


links = Table(
    "links",
    metadata,
    _id(),
    Column("title", String(80), nullable=False),
    Column("url", Text, nullable=False),
    _text("description", 240),
    Column("image_url", Text, nullable=False),
    _text("category", 40),
    _text("accent", 24),
    _position(),
    _text("created_at", 48),
    **_MYSQL,
)

log_sources = Table(
    "log_sources",
    metadata,
    _id(),
    Column("name", String(80), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("config", Text, nullable=False),
    _text("created_at", 48),
    **_MYSQL,
)

log_filters = Table(
    "log_filters",
    metadata,
    _id(),
    Column("name", String(80), nullable=False),
    Column("source_id", CHAR(32), nullable=False, server_default=""),
    Column("expression", Text, nullable=False),
    _text("freshness", 8, "1h"),
    Column("limit", Integer, nullable=False, server_default="50"),
    _text("description", 160),
    _text("line_format", 240),
    Column("watch", Boolean, nullable=False, server_default="0"),
    _text("last_seen_at", 48),
    _position(),
    _text("created_at", 48),
    **_MYSQL,
)

github_feeds = Table(
    "github_feeds",
    metadata,
    _id(),
    Column("name", String(80), nullable=False),
    Column("org", String(40), nullable=False),
    Column("repos", Text, nullable=False),
    _text("event_kinds", 160),
    Column("hide_own", Boolean, nullable=False, server_default="0"),
    Column("hide_bots", Boolean, nullable=False, server_default="1"),
    _text("description", 160),
    _text("last_seen_at", 48),
    _position(),
    _text("created_at", 48),
    **_MYSQL,
)

# preferences as name/value rows, so a new one needs no migration
settings = Table(
    "settings",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    **_MYSQL,
)
