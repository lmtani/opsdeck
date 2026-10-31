"""Composition root: wires concrete implementations into the services.

This is the only place that knows both sides — domain interfaces and the
concrete implementations — and the only place that reads the environment
(through Settings.from_env). Storage and command runner are chosen here:

- storage: a SQL database through SQLAlchemy: a SQLite file in the data
  directory by default, MySQL with DASHBOARD_DB_URL or DASHBOARD_DB_HOST.
- logs: one provider per kind of log source; GCP sources read Cloud Logging
  through its API client with Application Default Credentials.
- commands: real subprocess by default, disabled with DASHBOARD_COMMANDS=off.
- github: REST/GraphQL API with GITHUB_TOKEN or, locally, the
  token of the `gh` CLI login.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from app.application.github import GithubActivityService, GithubFeedService
from app.application.links import CommandService, ImageService, LinkService
from app.application.logs import LogFilterService, LogQueryService, LogSourceService, LogWatchService
from app.application.settings import SettingsService
from app.application.sharing import SharingService
from app.config import Settings
from app.domain.github import GithubFeedRepository
from app.domain.links import CommandRunner, LinkRepository
from app.domain.logs import LogFilterRepository, LogProvider, LogSourceKind, LogSourceRepository
from app.domain.settings import SettingsRepository
from app.infrastructure.github import GithubApiProvider, resolve_github_token
from app.infrastructure.host import DisabledCommandRunner, LocalImageStore, SubprocessCommandRunner
from app.infrastructure.logs.gcp import CloudLoggingApiProvider
from app.infrastructure.persistence.sql import (
    SqlGithubFeedRepository,
    SqlLinkRepository,
    SqlLogFilterRepository,
    SqlLogSourceRepository,
    SqlSettingsRepository,
    create_database_engine,
    run_migrations,
)
from app.presentation.api import Services
from app.presentation.api import create_app as _create_app

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"


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


def _build_command_runner(settings: Settings) -> CommandRunner:
    if not settings.commands_enabled:
        return DisabledCommandRunner()
    return SubprocessCommandRunner()


def build_services(data_dir: Path = DATA_DIR, settings: Settings | None = None) -> Services:
    settings = settings or Settings.from_env()
    repositories = _build_repositories(data_dir, settings)
    github_provider = GithubApiProvider(token_source=lambda: resolve_github_token(settings.github_token))
    images = ImageService(LocalImageStore(data_dir / "images"))
    log_providers = _build_log_providers(settings)
    return Services(
        links=LinkService(repositories.links),
        commands=CommandService(_build_command_runner(settings), repositories.links),
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
    )


def create_app(
    data_dir: Path = DATA_DIR, web_dir: Path = WEB_DIR, settings: Settings | None = None
) -> FastAPI:
    return _create_app(
        services=build_services(data_dir, settings),
        web_dir=web_dir,
        images_dir=data_dir / "images",
    )
