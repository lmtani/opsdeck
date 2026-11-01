"""The SQL adapter on a real SQLite file: migrations, the repositories'
upsert/delete semantics and the schema/migration agreement. With
OPSDECK_TEST_MYSQL_URL (a server URL whose database may be dropped, e.g.
mysql+pymysql://root:1234@127.0.0.1:13306/opsdeck_test) the same tests also
run on MySQL, whose upsert is spelled differently."""

import os
from datetime import timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from opsdeck.domain.github import EventKind, GithubFeed
from opsdeck.domain.links import Link, LinkKind
from opsdeck.domain.logs import Freshness, LogFilter, LogSource
from opsdeck.domain.settings import AppSettings
from opsdeck.infrastructure.persistence.sql import (
    SqlGithubFeedRepository,
    SqlLinkRepository,
    SqlLogFilterRepository,
    SqlLogSourceRepository,
    SqlSettingsRepository,
    create_database_engine,
    run_migrations,
)
from opsdeck.infrastructure.persistence.sql.tables import metadata
from tests.fakes import ts

MYSQL_URL = os.environ.get("OPSDECK_TEST_MYSQL_URL", "")


def _drop_mysql_database(url: str) -> None:
    parsed = make_url(url)
    server = create_engine(parsed.set(database=""))
    with server.begin() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS `{parsed.database}`"))
    server.dispose()


@pytest.fixture(params=["sqlite", "mysql"])
def engine(request, tmp_path):
    if request.param == "mysql":
        if not MYSQL_URL:
            pytest.skip("set OPSDECK_TEST_MYSQL_URL to run on MySQL")
        _drop_mysql_database(MYSQL_URL)
        url = MYSQL_URL
    else:
        url = f"sqlite:///{tmp_path / 'nested' / 'opsdeck.db'}"
    engine = create_database_engine(url)
    assert run_migrations(engine) == ["0001"]
    yield engine
    engine.dispose()
    if request.param == "mysql":
        _drop_mysql_database(MYSQL_URL)


def test_migrations_build_exactly_the_declared_schema(engine):
    assert run_migrations(engine) == []  # nothing pending the second time
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        assert MigrationContext.configure(connection).get_current_revision() == "0001"


def test_a_database_of_the_plain_sql_migrations_is_adopted(tmp_path):
    """Built by 0001-0008: stamped at the baseline, nothing recreated."""
    engine = create_database_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE schema_migrations (version VARCHAR(255) PRIMARY KEY)"))
        for number in range(1, 9):
            connection.execute(text("INSERT INTO schema_migrations VALUES (:v)"), {"v": f"000{number}_x.sql"})
        connection.execute(text("DELETE FROM schema_migrations WHERE version = '0008_x.sql'"))
        connection.execute(text("INSERT INTO schema_migrations VALUES ('0008_add_log_filter_watch.sql')"))
    SqlLinkRepository(engine).save(Link.new(title="kept", url="https://x"))

    assert run_migrations(engine) == []
    assert [link.title for link in SqlLinkRepository(engine).list_all()] == ["kept"]
    assert "schema_migrations" not in inspect(engine).get_table_names()


def test_a_database_left_at_an_older_plain_sql_migration_is_refused(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE schema_migrations (version VARCHAR(255) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO schema_migrations VALUES ('0006_add_github_feeds.sql')"))
    with pytest.raises(RuntimeError, match="older schema"):
        run_migrations(engine)


def test_links_save_updates_in_place_and_delete_reports_a_hit(engine):
    links = SqlLinkRepository(engine)
    first = Link.new(title="IGV", kind="command", command="igv.sh")
    second = Link.new(title="Console", url="https://console.cloud.google.com")
    second.created_at = first.created_at + timedelta(seconds=1)
    links.save(first)
    links.save(second)

    first.title, first.position = "IGV 2", 3
    original = first.created_at
    first.created_at = original + timedelta(days=1)  # an upsert never rewrites created_at
    links.save(first)
    stored = links.get(first.id)
    assert (stored.title, stored.kind, stored.position, stored.created_at) == (
        "IGV 2",
        LinkKind.COMMAND,
        3,
        original,
    )
    assert [link.id for link in links.list_all()] == [first.id, second.id]  # by created_at
    assert links.delete(first.id) is True and links.delete(first.id) is False
    assert links.get(first.id) is None


def test_filters_and_sources_roundtrip(engine):
    sources, filters = SqlLogSourceRepository(engine), SqlLogFilterRepository(engine)
    source = LogSource.new(name="Prod", kind="gcp", config={"project": "proj-é"})
    sources.save(source)
    assert sources.get(source.id) == source

    log_filter = LogFilter.new(
        name="errors", source_id=source.id, expression="severity>=ERROR", freshness="6h"
    )
    log_filter.set_watch(True, ts("2026-10-31T10:00:00Z"))
    filters.save(log_filter)
    stored = filters.get(log_filter.id)
    assert stored == log_filter
    assert (stored.freshness, stored.watch, stored.last_seen_at) == (
        Freshness.H6,
        True,
        ts("2026-10-31T10:00:00Z"),
    )


def test_feeds_keep_their_lists_and_flags(engine):
    feeds = SqlGithubFeedRepository(engine)
    feed = GithubFeed.new(name="Core", org="acme", repos=["core", "api"], event_kinds=["pr", "review"])
    feeds.save(feed)
    stored = feeds.get(feed.id)
    assert stored == feed
    assert stored.event_kinds == [EventKind.PR, EventKind.REVIEW] and stored.hide_bots is True

    empty = GithubFeed.new(name="Org", org="acme")
    feeds.save(empty)
    assert feeds.get(empty.id).repos == [] and feeds.get(empty.id).event_kinds == []


def test_settings_default_until_saved_then_read_back(engine):
    repository = SqlSettingsRepository(engine)
    assert repository.load() == AppSettings()
    repository.save(AppSettings(theme="paper", polling=False, logs_every=600))
    repository.save(AppSettings(theme="ember", polling=False, logs_every=600))  # upsert by name
    assert repository.load() == AppSettings(theme="ember", polling=False, logs_every=600)
