"""The `opsdeck` command.

    opsdeck                        # serve at http://127.0.0.1:8765
    opsdeck serve --port 9000      # the same, elsewhere
    opsdeck info                   # version, data directory and database
    opsdeck migrate                # apply the pending database migrations
    opsdeck revision "add x"       # (development) draft the next migration from tables.py

The address, the database and the data directory come from the environment
(DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_DB_URL, OPSDECK_DATA_DIR...); the
flags override the address.
"""

import argparse
from importlib.metadata import version

import uvicorn
from sqlalchemy.engine import make_url

from opsdeck.bootstrap import create_app, database_url, prepare_data_dir, resolve_data_dir
from opsdeck.config import Settings
from opsdeck.infrastructure.persistence.sql import create_database_engine, new_revision, run_migrations


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
    revision = commands.add_parser("revision", help="(development) draft the next migration")
    revision.add_argument("message", help="what the migration does, e.g. 'add link color'")
    return parser


def main(argv: list[str] | None = None) -> None:
    settings = Settings.from_env()
    arguments = _parser(settings).parse_args(argv)
    if arguments.command == "info":
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
