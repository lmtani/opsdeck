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
from app.infrastructure.persistence.json_files import (
    JsonLinkRepository,
    JsonLogFilterRepository,
    JsonLogSourceRepository,
    JsonSettingsRepository,
    adopt_legacy_projects,
)
from app.infrastructure.persistence.mysql import (
    MySqlConnectionFactory,
    MySqlLinkRepository,
    MySqlLogFilterRepository,
    MySqlLogSourceRepository,
    MySqlSettingsRepository,
    run_migrations,
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


def _import_sources(factory: MySqlConnectionFactory) -> dict[str, str]:
    """Copy the JSON log sources, reusing a MySQL source that reads the same
    place; returns JSON source id -> MySQL source id."""
    json_sources = JsonLogSourceRepository(DATA_DIR / "log_sources.json")
    adopt_legacy_projects(DATA_DIR / "filters.json", json_sources)
    target = MySqlLogSourceRepository(factory)
    existing = target.list_all()
    ids, imported = {}, 0
    for source in json_sources.list_all():
        match = next((item for item in existing if item.same_place(source.kind, source.config)), None)
        if match is None:
            target.save(source)
            existing.append(source)
            match, imported = source, imported + 1
        ids[source.id] = match.id
    print(f"  log sources: {imported} imported")
    return ids


def import_json_data(factory: MySqlConnectionFactory) -> None:
    source_ids = _import_sources(factory)
    _copy_missing([("links", JsonLinkRepository(DATA_DIR / "links.json"), MySqlLinkRepository(factory))])
    filters = MySqlLogFilterRepository(factory)
    existing = {item.id for item in filters.list_all()}
    imported = 0
    for log_filter in JsonLogFilterRepository(DATA_DIR / "filters.json").list_all():
        if log_filter.id not in existing:
            log_filter.source_id = source_ids.get(log_filter.source_id, log_filter.source_id)
            filters.save(log_filter)
            imported += 1
    print(f"  filters: {imported} imported")
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
