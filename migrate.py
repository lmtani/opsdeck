"""Apply pending MySQL migrations: `uv run migrate.py`.

With --import-json, also copies records from the legacy data/*.json files.

The app also applies pending migrations automatically on startup; this
script exists for running them explicitly.
"""

import argparse
from dataclasses import asdict

from app.bootstrap import DATA_DIR
from app.config import Settings
from app.domain.settings import AppSettings
from app.infrastructure.json_repositories import (
    JsonLinkRepository,
    JsonLogFilterRepository,
    JsonSettingsRepository,
)
from app.infrastructure.migrations import run_migrations
from app.infrastructure.mysql_connection import MySqlConnectionFactory
from app.infrastructure.mysql_repositories import (
    MySqlLinkRepository,
    MySqlLogFilterRepository,
    MySqlSettingsRepository,
)


def _copy_missing(pairs) -> None:
    for label, source, target in pairs:
        existing = {item.id for item in target.list_all()}
        imported = 0
        for item in source.list_all():
            if item.id not in existing:
                target.save(item)
                imported += 1
        print(f"  {label}: {imported} imported")


def import_json_data(factory: MySqlConnectionFactory) -> None:
    _copy_missing(
        [
            ("links", JsonLinkRepository(DATA_DIR / "links.json"), MySqlLinkRepository(factory)),
            (
                "filters",
                JsonLogFilterRepository(DATA_DIR / "filters.json"),
                MySqlLogFilterRepository(factory),
            ),
        ]
    )
    json_settings = JsonSettingsRepository(DATA_DIR / "settings.json").load()
    if asdict(json_settings) != asdict(AppSettings()):
        MySqlSettingsRepository(factory).save(json_settings)
        print(f"  settings: theme '{json_settings.theme}' imported")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--import-json",
        action="store_true",
        help="also import legacy records from data/*.json",
    )
    arguments = parser.parse_args()

    factory = MySqlConnectionFactory(Settings.from_env().mysql)
    applied = run_migrations(factory)

    if applied:
        print("Applied migrations:")
        for name in applied:
            print(f"  {name}")
    else:
        print("No pending migrations.")

    if arguments.import_json:
        print("Importing data/*.json:")
        import_json_data(factory)


if __name__ == "__main__":
    main()
