"""The `opsdeck` command.

    opsdeck                        # serve at http://127.0.0.1:8765
    opsdeck serve --port 9000      # the same, elsewhere
    opsdeck info                   # version, data directory and database
    opsdeck migrate                # apply the pending database migrations
    opsdeck service install        # start OpsDeck at login, in the background
    opsdeck service uninstall      # stop it and remove that again
    opsdeck revision "add x"       # (development) draft the next migration from tables.py

The address, the database and the data directory come from the environment
(DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_DB_URL, OPSDECK_DATA_DIR...); the
flags override the address.
"""

import argparse
import os
import shlex
import sys
from importlib.metadata import version

import uvicorn
from sqlalchemy.engine import make_url

from opsdeck.bootstrap import create_app, database_url, prepare_data_dir, resolve_data_dir
from opsdeck.config import Settings
from opsdeck.infrastructure.persistence.sql import create_database_engine, new_revision, run_migrations
from opsdeck.service import (
    PASSED_ENV,
    ServiceError,
    manager_for,
    port_free,
    spec_for,
    wait_until_up,
)


def _parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opsdeck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"opsdeck {version('opsdeck')}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="serve the dashboard (the default)")
    parser.add_argument("--host", default=settings.host, help=f"default {settings.host}")
    parser.add_argument("--port", type=int, default=settings.port, help=f"default {settings.port}")
    # SUPPRESS: `opsdeck --port 9000 serve` keeps the port given before the command
    serve.add_argument("--host", default=argparse.SUPPRESS, help=f"default {settings.host}")
    serve.add_argument("--port", type=int, default=argparse.SUPPRESS, help=f"default {settings.port}")
    commands.add_parser("info", help="show the version, the data directory and the database")
    commands.add_parser("migrate", help="apply the pending database migrations")
    service = commands.add_parser("service", help="run OpsDeck in the background, started at login")
    actions = service.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install", help="install and start the service (again, to apply changes)")
    install.add_argument("--host", default=settings.host, help=f"default {settings.host}")
    install.add_argument("--port", type=int, default=settings.port, help=f"default {settings.port}")
    actions.add_parser("uninstall", help="stop the service and remove it; the data stays")
    actions.add_parser("restart", help="restart the service, e.g. after `uv tool upgrade opsdeck`")
    actions.add_parser("status", help="whether the service is installed and running")
    revision = commands.add_parser("revision", help="(development) draft the next migration")
    revision.add_argument("message", help="what the migration does, e.g. 'add link color'")
    return parser


def _install(host: str, port: int) -> None:
    manager = manager_for()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Warning: listening on {host} makes OpsDeck reachable from other machines.")
    if not manager.running() and not port_free(host, port):
        raise ServiceError(
            f"Port {port} is already in use (is `opsdeck` running in a terminal?)."
            " Stop it, or choose another port with --port."
        )
    spec = spec_for(host, port, os.environ)
    manager.install(spec)
    print(f"Installed the {manager.name}: {manager.path}")
    print(f"  runs {shlex.join(spec.command)}")
    passed = [name for name in PASSED_ENV if name in spec.env and name != "PATH"]
    if passed:
        print(f"  with {', '.join(passed)} from this shell (reinstall to change them)")
    if "/uv/tools/" not in spec.command[0]:
        print("  (not a `uv tool` install: the service runs from this environment, so keep it where it is)")
    print("It starts when you log in and restarts if it stops unexpectedly.")
    if sys.platform.startswith("linux"):
        print("To start it at boot, before you log in: loginctl enable-linger")
    if wait_until_up(spec.url):
        print(f"OpsDeck ▸ {spec.url}")
    else:
        print(f"It has not answered at {spec.url} yet; see the logs: {manager.logs}")
    print("Remove it with: opsdeck service uninstall")


def _service(action: str, settings: Settings, arguments: argparse.Namespace) -> None:
    if action == "install":
        _install(arguments.host, arguments.port)
        return
    manager = manager_for()
    if action == "status":
        if not manager.installed():
            print("The service is not installed (opsdeck service install).")
            return
        print(f"{manager.name}: {manager.path}")
        print(manager.status(), end="")
        print(f"logs: {manager.logs}")
        return
    if not manager.installed():
        print("The service is not installed; nothing to do.")
        return
    if action == "restart":
        manager.restart()
        print("Restarted the service.")
        return
    manager.uninstall()
    print(f"Stopped and removed the {manager.name}; OpsDeck no longer starts at login.")
    print(f"The data stays in {resolve_data_dir(settings)}. To remove OpsDeck completely:")
    print("  uv tool uninstall opsdeck")
    print(f"  rm -rf {shlex.quote(str(resolve_data_dir(settings)))}")


def main(argv: list[str] | None = None) -> None:
    settings = Settings.from_env()
    arguments = _parser(settings).parse_args(argv)
    if arguments.command == "service":
        try:
            _service(arguments.action, settings, arguments)
        except ServiceError as error:
            sys.exit(f"opsdeck: {error}")
    elif arguments.command == "info":
        directory = resolve_data_dir(settings)
        url = make_url(database_url(directory, settings)).render_as_string(hide_password=True)
        print(f"opsdeck {version('opsdeck')}")
        print(f"data directory: {directory}")
        print(f"database:       {url}")
    elif arguments.command in ("migrate", "revision"):
        engine = create_database_engine(database_url(prepare_data_dir(settings), settings))
        applied = run_migrations(engine)  # a draft is diffed against an up-to-date database
        if arguments.command == "revision":
            new_revision(engine, arguments.message)
            print("Drafted a migration in persistence/sql/migrations/versions/; review it.")
        else:
            print("Applied migrations: " + ", ".join(applied) if applied else "No pending migrations.")
    else:
        app = create_app(settings)
        print(f"OpsDeck ▸ http://{arguments.host}:{arguments.port}")
        uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="warning")
