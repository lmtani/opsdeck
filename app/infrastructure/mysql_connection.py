"""MySQL connection factory shared by repositories and the migration runner."""

from contextlib import contextmanager
from dataclasses import dataclass

import pymysql
import pymysql.cursors


@dataclass(frozen=True)
class MySqlSettings:
    host: str = "127.0.0.1"
    port: int = 13306
    user: str = "root"
    password: str = "1234"
    database: str = "opsdeck"


class MySqlConnectionFactory:
    """Opens short-lived connections (cheap on localhost, no stale-socket issues)."""

    def __init__(self, config: MySqlSettings):
        self._config = config

    @property
    def config(self) -> MySqlSettings:
        return self._config

    @contextmanager
    def connect(self, with_database: bool = True):
        connection = pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            password=self._config.password,
            database=self._config.database if with_database else None,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
