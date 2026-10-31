"""Repositories on MySQL 8 (the default backend): short-lived PyMySQL
connections, plus the runner of the numbered .sql files in migrations/mysql/."""

from app.infrastructure.persistence.mysql.connection import MySqlConnectionFactory, MySqlSettings
from app.infrastructure.persistence.mysql.migrations import run_migrations
from app.infrastructure.persistence.mysql.repositories import (
    MySqlGithubFeedRepository,
    MySqlLinkRepository,
    MySqlLogFilterRepository,
    MySqlLogSourceRepository,
    MySqlSettingsRepository,
)

__all__ = [
    "MySqlConnectionFactory",
    "MySqlGithubFeedRepository",
    "MySqlLinkRepository",
    "MySqlLogFilterRepository",
    "MySqlLogSourceRepository",
    "MySqlSettings",
    "MySqlSettingsRepository",
    "run_migrations",
]
