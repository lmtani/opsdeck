"""Settings come from the environment once, with local defaults."""

from app.config import Settings
from app.infrastructure.github.token import resolve_github_token


def test_defaults_use_the_sqlite_file_and_local_tools():
    settings = Settings.from_env({})
    assert settings.database_url == ""  # bootstrap puts a SQLite file in the data directory
    assert settings.commands_enabled is True and settings.github_token == ""


def test_a_database_host_means_mysql():
    """docker-compose points at its MySQL through DASHBOARD_DB_* variables."""
    settings = Settings.from_env(
        {
            "DASHBOARD_DB_HOST": "db",
            "DASHBOARD_DB_PASSWORD": "s3cr@t/x",
            "DASHBOARD_COMMANDS": "off",
            "GITHUB_TOKEN": "ghp_x",
        }
    )
    assert settings.database_url == "mysql+pymysql://root:s3cr%40t%2Fx@db:3306/opsdeck"
    assert settings.commands_enabled is False
    assert settings.github_token == "ghp_x"
    assert Settings.from_env({"GH_TOKEN": "gho_y"}).github_token == "gho_y"
    full = Settings.from_env(
        {"DASHBOARD_DB_HOST": "h", "DASHBOARD_DB_PORT": "13306", "DASHBOARD_DB_NAME": "x"}
    )
    assert full.database_url == "mysql+pymysql://root@h:13306/x"


def test_an_explicit_url_wins():
    env = {"DASHBOARD_DB_URL": "sqlite:////tmp/o.db", "DASHBOARD_DB_HOST": "db"}
    assert Settings.from_env(env).database_url == "sqlite:////tmp/o.db"


def test_github_token_prefers_the_configured_one(monkeypatch):
    assert resolve_github_token("configured") == "configured"
    monkeypatch.setattr("app.infrastructure.github.token.shutil.which", lambda name: None)
    assert resolve_github_token("") == ""  # no gh CLI either


def test_credentials_file_comes_from_the_standard_variable():
    assert Settings.from_env({}).gcp_credentials_file == ""
    settings = Settings.from_env({"GOOGLE_APPLICATION_CREDENTIALS": "/secrets/gcp-adc.json"})
    assert settings.gcp_credentials_file == "/secrets/gcp-adc.json"
