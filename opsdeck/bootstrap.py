"""Composition root: wires concrete implementations into the services.

This is the only place that knows both sides — domain interfaces and the
concrete implementations — and the only place that reads the environment
(through Settings.from_env). The adapters are chosen here:

- storage: a SQL database through SQLAlchemy: a SQLite file in the data
  directory by default, MySQL with DASHBOARD_DB_URL or DASHBOARD_DB_HOST.
  The data directory (that file and the uploaded images) is the user's data
  directory, or OPSDECK_DATA_DIR.
- logs: one provider per kind of log source; GCP sources read Cloud Logging
  through its API client with Application Default Credentials.
- github: REST/GraphQL API with GITHUB_TOKEN or, locally, the
  token of the `gh` CLI login.
- updates: the latest GitHub release of OpsDeck itself, unless
  OPSDECK_UPDATE_CHECK=off.
"""

import logging
import shutil
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI
from platformdirs import user_data_path

from opsdeck.application.backup import BackupService
from opsdeck.application.github import GithubActivityService, GithubFeedService
from opsdeck.application.links import ImageService, LinkService
from opsdeck.application.logs import LogFilterService, LogQueryService, LogSourceService, LogWatchService
from opsdeck.application.settings import SettingsService
from opsdeck.application.sharing import SharingService
from opsdeck.application.updates import UpdateService
from opsdeck.config import Settings
from opsdeck.domain.github import GithubFeedRepository
from opsdeck.domain.links import LinkRepository
from opsdeck.domain.logs import LogFilterRepository, LogProvider, LogSourceKind, LogSourceRepository
from opsdeck.domain.settings import SettingsRepository
from opsdeck.infrastructure.github import GithubApiProvider, GithubReleaseFeed, resolve_github_token
from opsdeck.infrastructure.host import LocalImageStore
from opsdeck.infrastructure.logs.gcp import CloudLoggingApiProvider
from opsdeck.infrastructure.persistence.sql import (
    SqlGithubFeedRepository,
    SqlLinkRepository,
    SqlLogFilterRepository,
    SqlLogSourceRepository,
    SqlSettingsRepository,
    create_database_engine,
    run_migrations,
)
from opsdeck.presentation.api import Services
from opsdeck.presentation.api import create_app as _create_app

WEB_DIR = Path(__file__).resolve().parent / "web"
RELEASES_REPO = "lmtani/opsdeck"
# where a clone kept its data before OpsDeck became an installable package
_LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_FILES = ("opsdeck.db", "opsdeck.db-wal", "opsdeck.db-shm")

logger = logging.getLogger(__name__)


def resolve_data_dir(settings: Settings) -> Path:
    """OPSDECK_DATA_DIR, else the user's data directory (~/.local/share/opsdeck
    on Linux, ~/Library/Application Support/opsdeck on macOS)."""
    if settings.data_dir:
        return Path(settings.data_dir).expanduser()
    return user_data_path("opsdeck", appauthor=False)


def adopt_legacy_data(target: Path, legacy: Path = _LEGACY_DATA_DIR) -> bool:
    """Copy the SQLite file and the images a clone kept in its data/ folder into
    a data directory that does not exist yet; the old folder stays as it was."""
    files = [legacy / name for name in _DATA_FILES if (legacy / name).is_file()]
    images = legacy / "images"
    if target.exists() or not (files or images.is_dir()):
        return False
    target.mkdir(parents=True)
    for file in files:
        shutil.copy2(file, target / file.name)
    if images.is_dir():
        shutil.copytree(images, target / "images")
    logger.warning("Copied the data in %s to %s; the old folder can be removed.", legacy, target)
    return True


@dataclass(frozen=True)
class _Repositories:
    links: LinkRepository
    filters: LogFilterRepository
    sources: LogSourceRepository
    settings: SettingsRepository
    feeds: GithubFeedRepository


def database_url(data_dir: Path, settings: Settings) -> str:
    return settings.database_url or f"sqlite:///{data_dir / 'opsdeck.db'}"


def _build_repositories(data_dir: Path, settings: Settings) -> _Repositories:
    engine = create_database_engine(database_url(data_dir, settings))
    run_migrations(engine)
    return _Repositories(
        links=SqlLinkRepository(engine),
        filters=SqlLogFilterRepository(engine),
        sources=SqlLogSourceRepository(engine),
        settings=SqlSettingsRepository(engine),
        feeds=SqlGithubFeedRepository(engine),
    )


def _build_log_providers(settings: Settings) -> dict[LogSourceKind, LogProvider]:
    return {LogSourceKind.GCP: CloudLoggingApiProvider(credentials_file=settings.gcp_credentials_file)}


def prepare_data_dir(settings: Settings) -> Path:
    """The data directory, created if missing; the default one first takes a
    clone's old data/ folder, if there is one."""
    directory = resolve_data_dir(settings)
    if not settings.data_dir:
        adopt_legacy_data(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_services(data_dir: Path, settings: Settings) -> Services:
    data_dir.mkdir(parents=True, exist_ok=True)
    repositories = _build_repositories(data_dir, settings)
    github_provider = GithubApiProvider(token_source=lambda: resolve_github_token(settings.github_token))
    images = ImageService(LocalImageStore(data_dir / "images"))
    log_providers = _build_log_providers(settings)
    return Services(
        links=LinkService(repositories.links),
        sources=LogSourceService(repositories.sources, repositories.filters),
        filters=LogFilterService(repositories.filters, repositories.sources),
        logs=LogQueryService(log_providers, repositories.sources, repositories.filters),
        watch=LogWatchService(log_providers, repositories.sources, repositories.filters),
        settings=SettingsService(repositories.settings),
        images=images,
        github_feeds=GithubFeedService(repositories.feeds),
        github=GithubActivityService(github_provider, repositories.feeds),
        sharing=SharingService(
            repositories.links, repositories.filters, repositories.sources, repositories.feeds, images
        ),
        backup=BackupService(
            repositories.links,
            repositories.sources,
            repositories.filters,
            repositories.feeds,
            repositories.settings,
            images,
        ),
        updates=UpdateService(
            GithubReleaseFeed(RELEASES_REPO), current=version("opsdeck"), enabled=settings.update_check
        ),
    )


def create_app(settings: Settings | None = None, web_dir: Path = WEB_DIR) -> FastAPI:
    """The whole app; called with no arguments (`uvicorn --factory`), it reads
    the environment."""
    settings = settings or Settings.from_env()
    directory = prepare_data_dir(settings)
    return _create_app(
        services=build_services(directory, settings),
        web_dir=web_dir,
        images_dir=directory / "images",
    )
