"""Runs the Alembic migrations in migrations/versions/ against an engine.

Databases built by the plain-SQL migrations that came before (a MySQL
`schema_migrations` table listing 0001-0008) already have the baseline
schema: they are stamped at revision 0001 and the old tracking table goes.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

SCRIPTS = Path(__file__).resolve().parent / "migrations"
BASELINE = "0001"
_LEGACY_LAST = "0008_add_log_filter_watch.sql"


def _config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPTS))
    config.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    config.attributes["connection"] = connection
    return config


def _current(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _adopt_legacy_schema(connection: Connection, config: Config) -> None:
    tables = set(inspect(connection).get_table_names())
    if "schema_migrations" not in tables or "alembic_version" in tables:
        return
    applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
    if _LEGACY_LAST not in applied:
        raise RuntimeError(
            f"This database stopped at an older schema ({max(applied, default='none')}); "
            "start an OpsDeck release from before the move to Alembic once to bring it to "
            f"{_LEGACY_LAST}, then this one."
        )
    command.stamp(config, BASELINE)
    connection.execute(text("DROP TABLE schema_migrations"))


def run_migrations(engine: Engine) -> list[str]:
    """Apply the pending migrations; return the revisions applied, oldest first."""
    with engine.begin() as connection:
        config = _config(connection)
        _adopt_legacy_schema(connection, config)
        before = _current(connection)
        command.upgrade(config, "head")
        after = _current(connection)
    if after == before:
        return []
    script = ScriptDirectory.from_config(config)
    return [item.revision for item in script.iterate_revisions(after, before)][::-1]


def new_revision(engine: Engine, message: str) -> None:
    """Write the next numbered migration, drafted from the difference between
    tables.py and the database (review it before committing)."""
    with engine.begin() as connection:
        config = _config(connection)
        script = ScriptDirectory.from_config(config)
        numbers = [int(item.revision) for item in script.walk_revisions() if item.revision.isdigit()]
        command.revision(config, message=message, autogenerate=True, rev_id=f"{max(numbers) + 1:04d}")
