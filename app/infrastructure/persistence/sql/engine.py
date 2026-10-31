"""The SQLAlchemy engine for a database URL: SQLite (the default, one file)
or MySQL 8 (mysql+pymysql://...)."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url


def _sqlite_pragmas(connection, _record) -> None:
    cursor = connection.cursor()
    # readers do not block the writer (the background counts write marks
    # while the page reads), and a writer waits for another instead of failing
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _create_mysql_database(url) -> None:
    """MySQL needs the database to exist before tables can be created in it."""
    server = create_engine(url.set(database=""))  # None would mean "unchanged"
    try:
        with server.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}`"
                    " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        server.dispose()


def create_database_engine(url: str) -> Engine:
    """An engine whose database exists: the SQLite file's folder is created,
    and so is a MySQL database that is missing."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(parsed)
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    if parsed.get_backend_name() == "mysql":
        parsed = parsed.update_query_dict({"charset": "utf8mb4"})
        _create_mysql_database(parsed)
        # a pooled connection may have been closed by the server meanwhile
        return create_engine(parsed, pool_pre_ping=True, pool_recycle=3600)
    raise ValueError(f"Unsupported database: {parsed.get_backend_name()} (use sqlite or mysql)")
