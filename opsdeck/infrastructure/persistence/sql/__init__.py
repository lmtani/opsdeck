"""Repositories on SQL databases through SQLAlchemy Core: SQLite (the
default, one file) or MySQL 8, chosen by the database URL. Implements every
repository port (LinkRepository, LogSourceRepository, LogFilterRepository,
GithubFeedRepository, SettingsRepository); the schema lives in tables.py and
its Alembic migrations in migrations/versions/."""

from opsdeck.infrastructure.persistence.sql.engine import create_database_engine
from opsdeck.infrastructure.persistence.sql.migrations import new_revision, run_migrations
from opsdeck.infrastructure.persistence.sql.repositories import (
    SqlGithubFeedRepository,
    SqlLinkRepository,
    SqlLogFilterRepository,
    SqlLogSourceRepository,
    SqlSettingsRepository,
)

__all__ = [
    "SqlGithubFeedRepository",
    "SqlLinkRepository",
    "SqlLogFilterRepository",
    "SqlLogSourceRepository",
    "SqlSettingsRepository",
    "create_database_engine",
    "new_revision",
    "run_migrations",
]
