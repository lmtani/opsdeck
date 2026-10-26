"""Runtime configuration: read from the environment once, by the composition
root, and handed to the adapters. Nothing else reads os.environ."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.infrastructure.mysql_connection import MySqlSettings

_OFF = ("0", "off", "false", "no")


@dataclass(frozen=True)
class Settings:
    mysql: MySqlSettings = field(default_factory=MySqlSettings)
    storage: str = "mysql"  # or "json": the legacy files under data/
    # "gcloud" runs the CLI; "api" uses the Cloud Logging client with the ADC
    # file at GOOGLE_APPLICATION_CREDENTIALS (containers have no CLI)
    log_provider: str = "gcloud"
    # command cards launch programs on the host: meaningless, and unsafe to
    # expose, inside a container, where this is turned off
    commands_enabled: bool = True
    # GitHub API token; empty = use the local `gh` login
    github_token: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        defaults = MySqlSettings()
        return cls(
            mysql=MySqlSettings(
                host=env.get("DASHBOARD_DB_HOST", defaults.host),
                port=int(env.get("DASHBOARD_DB_PORT", defaults.port)),
                user=env.get("DASHBOARD_DB_USER", defaults.user),
                password=env.get("DASHBOARD_DB_PASSWORD", defaults.password),
                database=env.get("DASHBOARD_DB_NAME", defaults.database),
            ),
            storage=env.get("DASHBOARD_STORAGE", "").lower() or "mysql",
            log_provider=env.get("DASHBOARD_LOG_PROVIDER", "").lower() or "gcloud",
            commands_enabled=env.get("DASHBOARD_COMMANDS", "").lower() not in _OFF,
            github_token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "",
        )
