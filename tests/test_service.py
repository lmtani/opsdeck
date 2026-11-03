"""`opsdeck service`: the unit and agent it writes, the commands it runs and
how it is removed. systemctl and launchctl are faked."""

import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

from opsdeck import cli, service
from opsdeck.service import (
    LABEL,
    SERVICE_MARKER,
    LaunchdAgent,
    ServiceError,
    SystemdUserService,
    manager_for,
    render_plist,
    render_unit,
    spec_for,
)

PYTHON = "/home/me/.local/share/uv/tools/opsdeck/bin/python"


class FakeRun:
    """Records each command; answers with `codes` (by the command's leading words) or 0."""

    def __init__(self, codes=None, stdout=""):
        self.calls: list[list[str]] = []
        self.codes = codes or {}
        self.stdout = stdout

    def __call__(self, command):
        self.calls.append(command)
        code = next((c for prefix, c in self.codes.items() if " ".join(command).startswith(prefix)), 0)
        return subprocess.CompletedProcess(command, code, stdout=self.stdout, stderr="boom" if code else "")


def test_the_spec_passes_only_opsdeck_variables_and_marks_the_service():
    environ = {
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "ghp_x",
        "HOME": "/home/me",
        "OPSDECK_DATA_DIR": "",
        "SECRET": "s",
    }
    spec = spec_for("127.0.0.1", 8765, environ, executable=PYTHON)
    assert spec.env == {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_x", SERVICE_MARKER: "1"}
    assert spec.command == [PYTHON, "-m", "opsdeck", "serve", "--host", "127.0.0.1", "--port", "8765"]
    assert spec.url == "http://127.0.0.1:8765"


def test_the_unit_quotes_its_words_and_doubles_percent_signs():
    environ = {"DASHBOARD_DB_URL": 'mysql+pymysql://root:p%40ss"w@h/opsdeck'}
    unit = render_unit(spec_for("127.0.0.1", 8765, environ, executable="/opt/my tools/python"))
    assert 'ExecStart="/opt/my tools/python" "-m" "opsdeck" "serve"' in unit
    assert 'Environment="DASHBOARD_DB_URL=mysql+pymysql://root:p%%40ss\\"w@h/opsdeck"' in unit
    assert f'Environment="{SERVICE_MARKER}=1"' in unit
    assert "Restart=on-failure" in unit and "WantedBy=default.target" in unit


def test_the_plist_runs_at_login_and_restarts_after_a_crash(tmp_path):
    spec = spec_for("127.0.0.1", 9000, {"PATH": "/opt/homebrew/bin:/usr/bin"}, executable=PYTHON)
    plist = plistlib.loads(render_plist(spec, tmp_path / "opsdeck.log"))
    assert plist["Label"] == LABEL and plist["ProgramArguments"] == spec.command
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert plist["StandardErrorPath"] == str(tmp_path / "opsdeck.log")


def test_systemd_install_writes_a_private_unit_enables_and_starts_it(tmp_path):
    run = FakeRun()
    manager = SystemdUserService(run, tmp_path)
    manager.install(spec_for("127.0.0.1", 8765, {"GITHUB_TOKEN": "ghp_x"}, executable=PYTHON))
    unit = tmp_path / "systemd" / "user" / "opsdeck.service"
    assert "ghp_x" in unit.read_text()
    assert stat.S_IMODE(unit.stat().st_mode) == 0o600  # it may hold tokens
    assert run.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "opsdeck.service"],
        ["systemctl", "--user", "restart", "opsdeck.service"],
    ]


def test_systemd_uninstall_stops_disables_and_deletes(tmp_path):
    run = FakeRun(codes={"systemctl --user reset-failed": 1})  # nothing failed: fine
    manager = SystemdUserService(run, tmp_path)
    manager.install(spec_for("127.0.0.1", 8765, {}, executable=PYTHON))
    run.calls.clear()
    manager.uninstall()
    assert not manager.installed()
    assert run.calls[0] == ["systemctl", "--user", "disable", "--now", "opsdeck.service"]
    assert ["systemctl", "--user", "daemon-reload"] in run.calls


def test_a_failing_systemctl_says_what_failed(tmp_path):
    manager = SystemdUserService(FakeRun(codes={"systemctl --user enable": 1}), tmp_path)
    with pytest.raises(ServiceError, match=r"`systemctl --user enable opsdeck\.service` failed: boom"):
        manager.install(spec_for("127.0.0.1", 8765, {}, executable=PYTHON))


def test_launchd_install_replaces_a_loaded_agent_and_uninstall_boots_it_out(tmp_path):
    run = FakeRun(stdout="state = running\npid = 42\n\tendpoints = {\n\t\tstate = active\n")
    agent = LaunchdAgent(run, tmp_path, uid=501)
    agent.install(spec_for("127.0.0.1", 8765, {}, executable=PYTHON))
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plistlib.loads(plist.read_bytes())["Label"] == LABEL
    assert ["launchctl", "bootout", f"gui/501/{LABEL}"] in run.calls  # was loaded: reloaded
    assert run.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(plist)]
    assert agent.running() and agent.status() == "state = running\npid = 42\n"

    agent.uninstall()
    assert not plist.exists()
    agent.restart()
    assert run.calls[-1] == ["launchctl", "kickstart", "-k", f"gui/501/{LABEL}"]


def test_the_manager_follows_the_platform(tmp_path):
    assert isinstance(manager_for("linux", {}, tmp_path, FakeRun()), SystemdUserService)
    assert isinstance(manager_for("darwin", {}, tmp_path, FakeRun()), LaunchdAgent)
    custom = manager_for("linux", {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}, tmp_path, FakeRun())
    assert custom.path == tmp_path / "cfg" / "systemd" / "user" / "opsdeck.service"
    with pytest.raises(ServiceError, match="No systemd user manager"):
        manager_for("linux", {}, tmp_path, FakeRun(codes={"systemctl --user show-environment": 1}))
    with pytest.raises(ServiceError, match=r"Linux \(systemd\) and macOS, not win32"):
        manager_for("win32", {}, tmp_path, FakeRun())


@pytest.fixture
def systemd(tmp_path, monkeypatch):
    run = FakeRun(codes={"systemctl --user is-active": 3})  # not running yet
    manager = SystemdUserService(run, tmp_path / "config")
    monkeypatch.setattr(cli, "manager_for", lambda: manager)
    monkeypatch.setattr(cli, "wait_until_up", lambda url: True)
    monkeypatch.setattr(cli, "port_free", lambda host, port: True)
    monkeypatch.setenv("OPSDECK_DATA_DIR", str(tmp_path / "data"))
    return manager


def test_cli_install_status_restart_uninstall(systemd, capsys):
    cli.main(["service", "install", "--port", "9100"])
    out = capsys.readouterr().out
    assert "Installed the systemd user service" in out and "OpsDeck ▸ http://127.0.0.1:9100" in out
    assert "opsdeck service uninstall" in out
    assert '"--port" "9100"' in systemd.path.read_text()

    cli.main(["service", "status"])
    assert "journalctl --user -u opsdeck" in capsys.readouterr().out
    cli.main(["service", "restart"])
    assert "Restarted" in capsys.readouterr().out

    cli.main(["service", "uninstall"])
    out = capsys.readouterr().out
    assert "no longer starts at login" in out and "uv tool uninstall opsdeck" in out
    assert "data" in out and not systemd.path.exists()
    cli.main(["service", "uninstall"])
    assert "not installed" in capsys.readouterr().out


def test_cli_install_refuses_a_port_in_use(systemd, monkeypatch):
    monkeypatch.setattr(cli, "port_free", lambda host, port: False)
    with pytest.raises(SystemExit, match="Port 8765 is already in use"):
        cli.main(["service", "install"])
    assert not systemd.installed()


def test_cli_install_warns_when_reachable_from_other_machines(systemd, capsys):
    cli.main(["service", "install", "--host", "0.0.0.0"])  # noqa: S104 - what the warning is about
    assert "reachable from other machines" in capsys.readouterr().out


def test_port_free_sees_a_listening_socket():
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert service.port_free("127.0.0.1", port) is False
    assert service.port_free("127.0.0.1", port) is True


def test_unit_path_is_under_the_config_home(tmp_path):
    assert SystemdUserService(FakeRun(), Path("/x")).path == Path("/x/systemd/user/opsdeck.service")
