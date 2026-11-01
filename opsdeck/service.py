"""OpsDeck as a background service of the user: started at login, restarted
if it crashes, removed with one command. No root needed.

- Linux: a systemd user unit, ~/.config/systemd/user/opsdeck.service
  (logs: journalctl --user -u opsdeck).
- macOS: a launchd agent, ~/Library/LaunchAgents/io.github.lmtani.opsdeck.plist
  (logs: ~/Library/Logs/opsdeck.log).

The service runs this same installation (the Python running `opsdeck service
install`, e.g. the uv tool environment, which `uv tool upgrade` updates in
place) with the address it was installed with. The OpsDeck variables set in
the installing shell (database, data directory, tokens) and its PATH, which
finds `gh`, are written into the service definition, readable only by the
user; reinstalling picks up changed ones.
"""

import os
import plistlib
import shlex
import socket
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

LABEL = "io.github.lmtani.opsdeck"
UNIT = "opsdeck.service"
# what Settings.from_env reads, plus PATH; the marker tells the app it runs as a service
PASSED_ENV = (
    "PATH",
    "OPSDECK_DATA_DIR",
    "OPSDECK_UPDATE_CHECK",
    "DASHBOARD_DB_URL",
    "DASHBOARD_DB_HOST",
    "DASHBOARD_DB_PORT",
    "DASHBOARD_DB_USER",
    "DASHBOARD_DB_PASSWORD",
    "DASHBOARD_DB_NAME",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
)
SERVICE_MARKER = "OPSDECK_SERVICE"

Run = Callable[[list[str]], subprocess.CompletedProcess]


class ServiceError(Exception):
    """The service cannot be installed or managed here; the message says why."""


def _run(command: list[str]) -> subprocess.CompletedProcess:
    # fixed programs (systemctl, launchctl) with arguments built here, no shell
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


@dataclass(frozen=True)
class ServiceSpec:
    command: list[str]  # the program and its arguments
    env: dict[str, str]
    url: str


def spec_for(
    host: str, port: int, environ: Mapping[str, str], executable: str = sys.executable
) -> ServiceSpec:
    env = {name: environ[name] for name in PASSED_ENV if environ.get(name)}
    env[SERVICE_MARKER] = "1"
    command = [executable, "-m", "opsdeck", "serve", "--host", host, "--port", str(port)]
    return ServiceSpec(command=command, env=env, url=f"http://{host}:{port}")


def _systemd_quote(value: str) -> str:
    """One word of a unit file: % doubled (specifier), backslashes and quotes escaped."""
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_unit(spec: ServiceSpec) -> str:
    environment = "".join(
        f"Environment={_systemd_quote(f'{name}={value}')}\n" for name, value in spec.env.items()
    )
    exec_start = " ".join(_systemd_quote(word) for word in spec.command)
    return (
        "# Written by `opsdeck service install`; remove with `opsdeck service uninstall`.\n"
        "[Unit]\n"
        "Description=OpsDeck, a personal devops dashboard\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        f"{environment}"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_plist(spec: ServiceSpec, log: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": spec.command,
            "EnvironmentVariables": spec.env,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after a clean stop
            "ThrottleInterval": 5,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
            "ProcessType": "Background",
        }
    )


def _write_private(path: Path, content: bytes) -> None:
    """Write a file only the user can read: it may hold tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(content)
    path.chmod(0o600)  # an existing file keeps its old mode otherwise


class ServiceManager(ABC):
    name: str  # what the user sees: "systemd user service", "launchd agent"
    path: Path  # the service definition
    logs: str  # how to read the logs

    def __init__(self, run: Run):
        self._run = run

    def _check(self, command: list[str]) -> str:
        result = self._run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ServiceError(f"`{shlex.join(command)}` failed: {detail}")
        return result.stdout

    def installed(self) -> bool:
        return self.path.exists()

    @abstractmethod
    def running(self) -> bool: ...

    @abstractmethod
    def install(self, spec: ServiceSpec) -> None:
        """Write the definition, enable it at login and (re)start it now."""

    @abstractmethod
    def uninstall(self) -> None:
        """Stop the service, disable it and delete the definition."""

    @abstractmethod
    def restart(self) -> None: ...

    @abstractmethod
    def status(self) -> str: ...


class SystemdUserService(ServiceManager):
    name = "systemd user service"

    def __init__(self, run: Run, config_home: Path):
        super().__init__(run)
        self.path = config_home / "systemd" / "user" / UNIT
        self.logs = "journalctl --user -u opsdeck -f"

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def running(self) -> bool:
        return self._run(self._systemctl("is-active", "--quiet", UNIT)).returncode == 0

    def install(self, spec: ServiceSpec) -> None:
        _write_private(self.path, render_unit(spec).encode())
        self._check(self._systemctl("daemon-reload"))
        self._check(self._systemctl("enable", UNIT))
        self._check(self._systemctl("restart", UNIT))  # starts it, or applies a changed unit

    def uninstall(self) -> None:
        self._run(self._systemctl("disable", "--now", UNIT))  # fine if already stopped
        self.path.unlink(missing_ok=True)
        self._check(self._systemctl("daemon-reload"))
        self._run(self._systemctl("reset-failed", UNIT))

    def restart(self) -> None:
        self._check(self._systemctl("restart", UNIT))

    def status(self) -> str:
        return self._run(self._systemctl("status", UNIT, "--no-pager", "--lines=5")).stdout


class LaunchdAgent(ServiceManager):
    name = "launchd agent"

    def __init__(self, run: Run, home: Path, uid: int):
        super().__init__(run)
        self.path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        self.log = home / "Library" / "Logs" / "opsdeck.log"
        self.logs = f"tail -f {shlex.quote(str(self.log))}"
        self._domain = f"gui/{uid}"
        self._target = f"{self._domain}/{LABEL}"

    def _loaded(self) -> bool:
        return self._run(["launchctl", "print", self._target]).returncode == 0

    def running(self) -> bool:
        result = self._run(["launchctl", "print", self._target])
        return result.returncode == 0 and "state = running" in result.stdout

    def install(self, spec: ServiceSpec) -> None:
        if self._loaded():
            self._run(["launchctl", "bootout", self._target])  # load the new definition below
        self.log.parent.mkdir(parents=True, exist_ok=True)
        _write_private(self.path, render_plist(spec, self.log))
        self._check(["launchctl", "bootstrap", self._domain, str(self.path)])

    def uninstall(self) -> None:
        if self._loaded():
            self._run(["launchctl", "bootout", self._target])
        self.path.unlink(missing_ok=True)

    def restart(self) -> None:
        self._check(["launchctl", "kickstart", "-k", self._target])

    def status(self) -> str:
        result = self._run(["launchctl", "print", self._target])
        if result.returncode != 0:
            return "not loaded\n"
        found: dict[str, str] = {}  # the agent's own fields come first; nested blocks repeat them
        for line in result.stdout.splitlines():
            key = next(
                (k for k in ("state =", "pid =", "last exit code =") if line.strip().startswith(k)), None
            )
            if key and key not in found:
                found[key] = line.strip()
        return "".join(f"{line}\n" for line in found.values())


def manager_for(
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    home: Path | None = None,
    run: Run = _run,
) -> ServiceManager:
    home = home or Path.home()
    if platform.startswith("linux"):
        config_home = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
        if run(["systemctl", "--user", "show-environment"]).returncode != 0:
            raise ServiceError(
                "No systemd user manager is reachable (`systemctl --user`). Run OpsDeck with `opsdeck`"
                " from your session's startup applications instead."
            )
        return SystemdUserService(run, config_home)
    if platform == "darwin":
        return LaunchdAgent(run, home, os.getuid())
    raise ServiceError(f"Running as a service is supported on Linux (systemd) and macOS, not {platform}.")


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, port)) != 0


def wait_until_up(url: str, seconds: float = 20, sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        sleep(0.5)
    return False
