"""Row mapping of the SQL repositories."""

from opsdeck.domain.github import EventKind, GithubFeed
from opsdeck.domain.links import Link, LinkKind
from opsdeck.domain.logs import Freshness, LogFilter, LogSource, LogSourceKind
from opsdeck.domain.settings import AppSettings
from opsdeck.infrastructure.persistence.rows import (
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


def test_settings_read_booleans_and_numbers_from_text():
    assert values_to_settings({}) == AppSettings()
    assert values_to_settings({"theme": "paper", "polling": "False"}) == AppSettings("paper", False)
    assert values_to_settings({"polling": "True"}).polling is True
    assert values_to_settings({"polling": False}).polling is False
    assert values_to_settings({"polling": True, "gone": "x"}) == AppSettings()
    # intervals come back from MySQL as text; garbage keeps the default
    assert values_to_settings({"logs_every": "600", "github_every": "soon"}).logs_every == 600
    assert values_to_settings({"github_every": "soon"}).github_every == 300
