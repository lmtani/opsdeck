"""Composition root: wires concrete implementations into the services.

This is the only place that knows both sides — domain interfaces and the
concrete implementations — and the only place that reads the environment
(through Settings.from_env). Storage, log provider and command runner are
chosen here:

- storage: MySQL (default), JSON files with DASHBOARD_STORAGE=json.
- logs: gcloud CLI by default, Cloud Logging API client with
  DASHBOARD_LOG_PROVIDER=api (containers).
- commands: real subprocess by default, disabled with DASHBOARD_COMMANDS=off.
- github: REST/GraphQL API with GITHUB_TOKEN or, locally, the
  token of the `gh` CLI login.
"""

from pathlib import Path

from fastapi import FastAPI

from app.application.github import GithubActivityService, GithubFeedService
from app.application.links import CommandService, ImageService, LinkService
from app.application.logs import LogFilterService, LogQueryService
from app.application.settings import SettingsService
from app.application.sharing import SharingService
from app.config import Settings
from app.domain.github import GithubFeedRepository
from app.domain.links import CommandRunner, LinkRepository
from app.domain.logs import LogFilterRepository, LogProvider
from app.domain.settings import SettingsRepository
from app.infrastructure.gcloud_log_provider import GcloudLogProvider
from app.infrastructure.github_api_provider import GithubApiProvider, resolve_github_token
from app.infrastructure.json_repositories import (
    JsonGithubFeedRepository,
    JsonLinkRepository,
    JsonLogFilterRepository,
    JsonSettingsRepository,
)
from app.infrastructure.local_image_store import LocalImageStore
from app.infrastructure.migrations import run_migrations
from app.infrastructure.mysql_connection import MySqlConnectionFactory
from app.infrastructure.mysql_repositories import (
    MySqlGithubFeedRepository,
    MySqlLinkRepository,
    MySqlLogFilterRepository,
    MySqlSettingsRepository,
)
from app.infrastructure.subprocess_command_runner import SubprocessCommandRunner
from app.presentation.api import Services
from app.presentation.api import create_app as _create_app

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"


def _build_repositories(
    data_dir: Path, settings: Settings
) -> tuple[LinkRepository, LogFilterRepository, SettingsRepository, GithubFeedRepository]:
    if settings.storage == "json":
        return (
            JsonLinkRepository(data_dir / "links.json"),
            JsonLogFilterRepository(data_dir / "filters.json"),
            JsonSettingsRepository(data_dir / "settings.json"),
            JsonGithubFeedRepository(data_dir / "github_feeds.json"),
        )
    mysql_factory = MySqlConnectionFactory(settings.mysql)
    run_migrations(mysql_factory)
    return (
        MySqlLinkRepository(mysql_factory),
        MySqlLogFilterRepository(mysql_factory),
        MySqlSettingsRepository(mysql_factory),
        MySqlGithubFeedRepository(mysql_factory),
    )


def _build_log_provider(settings: Settings) -> LogProvider:
    if settings.log_provider == "api":
        # imported lazily: pulls in grpc, which local gcloud runs don't need
        from app.infrastructure.cloud_logging_provider import CloudLoggingApiProvider

        return CloudLoggingApiProvider(credentials_file=settings.gcp_credentials_file)
    return GcloudLogProvider()


def _build_command_runner(settings: Settings) -> CommandRunner:
    if not settings.commands_enabled:
        from app.infrastructure.disabled_command_runner import DisabledCommandRunner

        return DisabledCommandRunner()
    return SubprocessCommandRunner()


def build_services(data_dir: Path = DATA_DIR, settings: Settings | None = None) -> Services:
    settings = settings or Settings.from_env()
    (
        link_repository,
        filter_repository,
        settings_repository,
        github_feed_repository,
    ) = _build_repositories(data_dir, settings)
    github_provider = GithubApiProvider(token_source=lambda: resolve_github_token(settings.github_token))
    images = ImageService(LocalImageStore(data_dir / "images"))
    return Services(
        links=LinkService(link_repository),
        commands=CommandService(_build_command_runner(settings), link_repository),
        filters=LogFilterService(filter_repository),
        logs=LogQueryService(_build_log_provider(settings), filter_repository),
        settings=SettingsService(settings_repository),
        images=images,
        github_feeds=GithubFeedService(github_feed_repository),
        github=GithubActivityService(github_provider, github_feed_repository),
        sharing=SharingService(link_repository, filter_repository, github_feed_repository, images),
    )


def create_app(
    data_dir: Path = DATA_DIR, web_dir: Path = WEB_DIR, settings: Settings | None = None
) -> FastAPI:
    return _create_app(
        services=build_services(data_dir, settings),
        web_dir=web_dir,
        images_dir=data_dir / "images",
    )
