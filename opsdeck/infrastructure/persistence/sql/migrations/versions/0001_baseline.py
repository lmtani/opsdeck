"""The schema as the plain-SQL migrations 0001-0008 left it.

A MySQL database those migrations built is stamped at this revision instead
of running it (see ../migrations.py); a new database, SQLite or MySQL, is
created by it.

Revision ID: 0001
Revises:
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MYSQL: dict[str, Any] = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _text(name: str, length: int, default: str = "") -> sa.Column:
    return sa.Column(name, sa.String(length), nullable=False, server_default=default)


def _common_tail() -> list[sa.Column]:
    return [
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        _text("created_at", 48),
    ]


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        _text("kind", 16, "url"),
        _text("command", 1024),
        _text("description", 240),
        sa.Column("image_url", sa.Text, nullable=False),
        _text("category", 40),
        _text("accent", 24),
        *_common_tail(),
        **MYSQL,
    )
    op.create_table(
        "log_sources",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("config", sa.Text, nullable=False),
        _text("created_at", 48),
        **MYSQL,
    )
    op.create_table(
        "log_filters",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("source_id", sa.CHAR(32), nullable=False, server_default=""),
        sa.Column("expression", sa.Text, nullable=False),
        _text("freshness", 8, "1h"),
        sa.Column("limit", sa.Integer, nullable=False, server_default="50"),
        _text("description", 160),
        _text("line_format", 240),
        sa.Column("watch", sa.Boolean, nullable=False, server_default="0"),
        _text("last_seen_at", 48),
        *_common_tail(),
        **MYSQL,
    )
    op.create_table(
        "github_feeds",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("org", sa.String(40), nullable=False),
        sa.Column("repos", sa.Text, nullable=False),
        _text("event_kinds", 160),
        sa.Column("hide_own", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("hide_bots", sa.Boolean, nullable=False, server_default="1"),
        _text("description", 160),
        _text("last_seen_at", 48),
        *_common_tail(),
        **MYSQL,
    )
    op.create_table(
        "settings",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        **MYSQL,
    )


def downgrade() -> None:
    for table in ("settings", "github_feeds", "log_filters", "log_sources", "links"):
        op.drop_table(table)
