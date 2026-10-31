"""Database migrations, explicitly (the app also applies them on startup).

    uv run migrate.py                      # apply the pending migrations
    uv run migrate.py revision "add x"     # draft the next one from tables.py

The database is the app's: DASHBOARD_DB_URL, DASHBOARD_DB_HOST (MySQL) or
the SQLite file in data/.
"""

import argparse

from app.bootstrap import DATA_DIR, database_url
from app.config import Settings
from app.infrastructure.persistence.sql import create_database_engine, new_revision, run_migrations


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command")
    revision = commands.add_parser("revision", help="draft the next migration from tables.py")
    revision.add_argument("message", help="what the migration does, e.g. 'add link color'")
    arguments = parser.parse_args()

    engine = create_database_engine(database_url(DATA_DIR, Settings.from_env()))
    applied = run_migrations(engine)  # a draft is diffed against an up-to-date database
    if arguments.command == "revision":
        new_revision(engine, arguments.message)
        print("Drafted a migration in app/infrastructure/persistence/sql/migrations/versions/; review it.")
    elif applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("No pending migrations.")


if __name__ == "__main__":
    main()
