"""Repositories on JSON files in data/ (DASHBOARD_STORAGE=json): the legacy
backend, kept as a fallback and as the source of `migrate.py --import-json`."""

from app.infrastructure.persistence.json_files.legacy import adopt_legacy_projects
from app.infrastructure.persistence.json_files.repositories import (
    JsonGithubFeedRepository,
    JsonLinkRepository,
    JsonLogFilterRepository,
    JsonLogSourceRepository,
    JsonSettingsRepository,
)

__all__ = [
    "JsonGithubFeedRepository",
    "JsonLinkRepository",
    "JsonLogFilterRepository",
    "JsonLogSourceRepository",
    "JsonSettingsRepository",
    "adopt_legacy_projects",
]
