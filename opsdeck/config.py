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
    # GitHub API token; empty = use the local `gh` login
    github_token: str = ""
    # where the SQLite file and the uploaded images live; empty = the user's
    # data directory (~/.local/share/opsdeck on Linux)
    data_dir: str = ""
    # address the server listens on
    host: str = "127.0.0.1"
    port: int = 8765
    # look for a newer OpsDeck release on GitHub (a few anonymous requests a day)
    update_check: bool = True
    # running as the service `opsdeck service install` set up (it sets OPSDECK_SERVICE=1)
    service: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            database_url=_database_url(env),
            gcp_credentials_file=env.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
            github_token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "",
            data_dir=env.get("OPSDECK_DATA_DIR", ""),
            host=env.get("DASHBOARD_HOST") or "127.0.0.1",
            port=int(env.get("DASHBOARD_PORT") or "8765"),
            update_check=env.get("OPSDECK_UPDATE_CHECK", "").lower() not in _OFF,
            service=env.get("OPSDECK_SERVICE", "") == "1",
        )
