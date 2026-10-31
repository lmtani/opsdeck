"""Settings come from the environment once, with local defaults."""

from app.config import Settings
from app.infrastructure.github.token import resolve_github_token


def test_defaults_match_the_local_setup():
    settings = Settings.from_env({})
    assert (settings.mysql.host, settings.mysql.port, settings.mysql.database) == (
        "127.0.0.1",
        13306,
        "opsdeck",
    )
    assert settings.storage == "mysql"
    assert settings.commands_enabled is True and settings.github_token == ""


def test_container_overrides():
    settings = Settings.from_env(
        {
            "DASHBOARD_DB_HOST": "mysql.infra.svc.cluster.local",
            "DASHBOARD_DB_PORT": "3306",
            "DASHBOARD_DB_PASSWORD": "s3cret",
            "DASHBOARD_COMMANDS": "off",
            "GITHUB_TOKEN": "ghp_x",
        }
    )
    assert (settings.mysql.host, settings.mysql.port, settings.mysql.password) == (
        "mysql.infra.svc.cluster.local",
        3306,
        "s3cret",
    )
    assert settings.commands_enabled is False
    assert settings.github_token == "ghp_x"
    assert Settings.from_env({"GH_TOKEN": "gho_y"}).github_token == "gho_y"


def test_github_token_prefers_the_configured_one(monkeypatch):
    assert resolve_github_token("configured") == "configured"
    monkeypatch.setattr("app.infrastructure.github.token.shutil.which", lambda name: None)
    assert resolve_github_token("") == ""  # no gh CLI either


def test_credentials_file_comes_from_the_standard_variable():
    assert Settings.from_env({}).gcp_credentials_file == ""
    settings = Settings.from_env({"GOOGLE_APPLICATION_CREDENTIALS": "/secrets/gcp-adc.json"})
    assert settings.gcp_credentials_file == "/secrets/gcp-adc.json"
