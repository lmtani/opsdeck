"""Row mapping shared by the SQL and JSON repositories, and the upgrade of
legacy JSON filters to log sources."""

import json

from app.domain.github import EventKind, GithubFeed
from app.domain.links import Link, LinkKind
from app.domain.logs import Freshness, LogFilter, LogSource, LogSourceKind
from app.domain.settings import AppSettings
from app.infrastructure.persistence.json_files import (
    JsonLogFilterRepository,
    JsonLogSourceRepository,
    adopt_legacy_projects,
)
from app.infrastructure.persistence.rows import (
    feed_to_row,
    filter_to_row,
    link_to_row,
    row_to_feed,
    row_to_filter,
    row_to_link,
    row_to_source,
    source_to_row,
    values_to_settings,
)


def test_link_and_filter_enums_become_plain_values_and_back():
    link = Link(id="l" * 32, title="IGV", kind=LinkKind.COMMAND, command="igv.sh")
    assert link_to_row(link)["kind"] == "command"
    assert row_to_link(link_to_row(link)) == link

    log_filter = LogFilter(
        id="f" * 32, name="Erros", source_id="s" * 32, expression="", freshness=Freshness.D3
    )
    assert filter_to_row(log_filter)["freshness"] == "3d"
    assert row_to_filter(filter_to_row(log_filter)) == log_filter


def test_source_kind_and_config_survive_a_roundtrip():
    source = LogSource(id="s" * 32, name="Prod", kind=LogSourceKind.GCP, config={"project": "p"})
    row = source_to_row(source)
    assert (row["kind"], row["config"]) == ("gcp", {"project": "p"})
    assert row_to_source(row) == source


def test_legacy_json_filters_get_a_source_per_project(tmp_path):
    filters_path = tmp_path / "filters.json"
    legacy = [
        {"id": "a" * 32, "name": "errors", "project": "p1", "expression": ""},
        {"id": "b" * 32, "name": "slow", "project": "p2", "expression": ""},
        {"id": "c" * 32, "name": "api", "project": "p1", "expression": ""},
    ]
    filters_path.write_text(json.dumps(legacy))
    sources = JsonLogSourceRepository(tmp_path / "log_sources.json")
    existing = LogSource.new(name="Billing", kind="gcp", config={"project": "p2"})
    sources.save(existing)

    assert adopt_legacy_projects(filters_path, sources) == 3
    assert adopt_legacy_projects(filters_path, sources) == 0  # already upgraded

    by_project = {source.config["project"]: source for source in sources.list_all()}
    assert sorted(by_project) == ["p1", "p2"] and by_project["p2"].id == existing.id
    assert by_project["p1"].name == "p1"
    filters = {item.name: item.source_id for item in JsonLogFilterRepository(filters_path).list_all()}
    assert filters == {"errors": by_project["p1"].id, "slow": existing.id, "api": by_project["p1"].id}


def test_feed_lists_booleans_and_kinds_survive_a_roundtrip():
    feed = GithubFeed(
        id="f" * 32,
        name="Core",
        org="acme",
        repos=["core", "api"],
        event_kinds=[EventKind.PR, EventKind.REVIEW],
        hide_own=True,
        hide_bots=False,
    )
    row = feed_to_row(feed)
    assert row["event_kinds"] == ["pr", "review"]
    assert row_to_feed(row) == feed


def test_mysql_tinyint_and_missing_lists_are_normalized():
    row = feed_to_row(GithubFeed(id="f" * 32, name="Org", org="acme"))
    row.update(hide_own=0, hide_bots=1, repos=None, event_kinds=None)
    feed = row_to_feed(row)
    assert feed.repos == [] and feed.event_kinds == []
    assert feed.hide_own is False and feed.hide_bots is True


def test_settings_read_booleans_from_mysql_text_or_json_values():
    assert values_to_settings({}) == AppSettings()
    assert values_to_settings({"theme": "paper", "github_polling": "False"}) == AppSettings("paper", False)
    assert values_to_settings({"github_polling": "True"}).github_polling is True
    assert values_to_settings({"github_polling": False}).github_polling is False
    assert values_to_settings({"github_polling": True, "gone": "x"}) == AppSettings()
