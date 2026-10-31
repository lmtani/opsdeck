"""Plain-SQL migration runner.

Migrations are numbered .sql files in migrations/mysql/ (e.g.
0002_add_x.sql), applied in filename order and tracked in the
schema_migrations table. Statements are split on ';' — keep semicolons out
of string literals.
"""

from pathlib import Path

from app.infrastructure.persistence.mysql.connection import MySqlConnectionFactory

# app/infrastructure/persistence/mysql/ -> repository root
MIGRATIONS_ROOT = Path(__file__).resolve().parents[4] / "migrations"

_MYSQL_TRACKING_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "  version VARCHAR(255) NOT NULL PRIMARY KEY,"
    "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
    ") ENGINE=InnoDB"
)


def ensure_database(factory: MySqlConnectionFactory) -> None:
    database = factory.config.database
    with factory.connect(with_database=False) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )


def run_migrations(factory: MySqlConnectionFactory, directory: Path | None = None) -> list[str]:
    """Apply pending MySQL migrations; return the names of the ones applied."""
    ensure_database(factory)
    return _apply_pending(factory, directory or MIGRATIONS_ROOT / "mysql", _MYSQL_TRACKING_TABLE)


def _apply_pending(factory, directory: Path, tracking_table_ddl: str) -> list[str]:
    applied: list[str] = []
    with factory.connect() as connection, connection.cursor() as cursor:
        cursor.execute(tracking_table_ddl)
        cursor.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in cursor.fetchall()}

        for sql_file in sorted(directory.glob("*.sql")):
            if sql_file.name in done:
                continue
            for statement in sql_file.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (sql_file.name,),
            )
            connection.commit()
            applied.append(sql_file.name)
    return applied
