"""The `opsdeck` command, short of serving."""

import pytest

from opsdeck import cli


@pytest.fixture
def data(tmp_path, monkeypatch):
    for name in ("DASHBOARD_DB_URL", "DASHBOARD_DB_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPSDECK_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_info_names_the_data_directory_and_hides_passwords(data, monkeypatch, capsys):
    cli.main(["info"])
    out = capsys.readouterr().out
    assert str(data) in out and f"sqlite:///{data / 'opsdeck.db'}" in out
    monkeypatch.setenv("DASHBOARD_DB_URL", "mysql+pymysql://root:s3cret@h/opsdeck")
    cli.main(["info"])
    out = capsys.readouterr().out
    assert "s3cret" not in out and "root:***@h" in out


def test_migrate_creates_the_database(data, capsys):
    cli.main(["migrate"])
    assert "Applied migrations: 0001" in capsys.readouterr().out
    assert (data / "opsdeck.db").is_file()
    cli.main(["migrate"])
    assert "No pending migrations." in capsys.readouterr().out


def test_serve_takes_the_address_before_or_after_the_command(data, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "create_app", lambda settings: "app")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.append((kw["host"], kw["port"])))
    cli.main([])
    cli.main(["--port", "9000", "serve"])
    cli.main(["serve", "--host", "10.0.0.5", "--port", "9001"])
    assert calls == [("127.0.0.1", 8765), ("127.0.0.1", 9000), ("10.0.0.5", 9001)]


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("opsdeck ")
