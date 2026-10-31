"""Runtime configuration: read from the environment once, by the composition
root, and handed to the adapters. Nothing else reads os.environ."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine import URL

_OFF = ("0", "off", "false", "no")


def _database_url(env: Mapping[str, str]) -> str:
    """DASHBOARD_DB_URL as given; else a MySQL URL from DASHBOARD_DB_HOST and
    its companions (how docker-compose points at its MySQL); else empty."""
    if env.get("DASHBOARD_DB_URL"):
        return env["DASHBOARD_DB_URL"]
    if not env.get("DASHBOARD_DB_HOST"):
        return ""
    return URL.create(
        "mysql+pymysql",
        username=env.get("DASHBOARD_DB_USER", "root"),
        password=env.get("DASHBOARD_DB_PASSWORD") or None,
        host=env["DASHBOARD_DB_HOST"],
        port=int(env.get("DASHBOARD_DB_PORT", "3306")),
        database=env.get("DASHBOARD_DB_NAME", "opsdeck"),
    ).render_as_string(hide_password=False)


@dataclass(frozen=True)
class Settings:
    # SQLAlchemy URL of the database; empty = a SQLite file in the data directory
    database_url: str = ""
    # Application Default Credentials file for the Cloud Logging client; empty
    # = the well-known location `gcloud auth application-default login` writes
    gcp_credentials_file: str = ""
    # command cards launch programs on the host: meaningless, and unsafe to
    # expose, inside a container, where this is turned off
    commands_enabled: bool = True
    # GitHub API token; empty = use the local `gh` login
    github_token: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            database_url=_database_url(env),
            gcp_credentials_file=env.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
            commands_enabled=env.get("DASHBOARD_COMMANDS", "").lower() not in _OFF,
            github_token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "",
        )
