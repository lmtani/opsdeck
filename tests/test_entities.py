"""Entities enforce their own invariants: invalid ones cannot be built."""

from datetime import timedelta

import pytest

from app.domain.common import utc_now
from app.domain.errors import ValidationError
from app.domain.github import EventKind, FeedSpec, GithubFeed
from app.domain.links import Link, LinkKind
from app.domain.logs import Freshness, LogFilter, LogQuery
from tests.fakes import ts

# ----- links -----------------------------------------------------------------


def test_link_new_trims_and_parses_kind():
    link = Link.new(title="  IGV ", kind="command", command=" igv.sh ")
    assert (link.title, link.kind, link.command, link.url) == ("IGV", LinkKind.COMMAND, "igv.sh", "")
    assert link.id and link.created_at.tzinfo is not None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (dict(title=" ", url="https://x"), "Title"),
        (dict(title="x", kind="url", url=" "), "URL"),
        (dict(title="x", kind="command", command=""), "Command"),
        (dict(title="x", kind="exec", url="https://x"), "Invalid card type"),
    ],
)
def test_invalid_links_cannot_be_built(fields, message):
    with pytest.raises(ValidationError, match=message):
        Link.new(**fields)
    with pytest.raises(ValidationError, match=message):
        Link(id="x", **fields)


def test_link_update_keeps_identity_and_rejects_invalid_changes_untouched():
    link = Link.new(title="Console", url="https://x", position=3)
    link.update(title=" Painel ", url="https://y", category=" GCP ")
    assert (link.title, link.url, link.category, link.position) == ("Painel", "https://y", "GCP", 3)

    with pytest.raises(ValidationError):
        link.update(title="Painel", kind="command", command="")
    assert (link.kind, link.url) == (LinkKind.URL, "https://y")  # nothing changed


# ----- logs ------------------------------------------------------------------


def test_log_filter_invariants_and_query():
    log_filter = LogFilter.new(name=" Erros ", project=" p ", expression=" e ", freshness="6h", limit=25)
    assert (log_filter.name, log_filter.project, log_filter.freshness) == ("Erros", "p", Freshness.H6)
    assert log_filter.query() == LogQuery(project="p", expression="e", freshness=Freshness.H6, limit=25)

    for fields in [
        dict(name=" ", project="p"),
        dict(name="n", project=" "),
        dict(name="n", project="p", freshness="2h"),
        dict(name="n", project="p", limit=0),
    ]:
        with pytest.raises(ValidationError):
            LogFilter.new(**fields)


def test_log_query_normalizes_and_validates():
    query = LogQuery(project=" proj ", expression=" x ", freshness="1d")
    assert (query.project, query.expression, query.freshness) == ("proj", "x", Freshness.D1)
    with pytest.raises(ValidationError):
        LogQuery(project="p", limit=501)


# ----- github ----------------------------------------------------------------


def test_feed_spec_normalizes_on_construction():
    spec = FeedSpec(
        " acme ",
        repos=("core", "acme/api", "https://github.com/acme/web.git", "CORE"),
        event_kinds=("review", "pr"),
    )
    assert spec.org == "acme"
    assert spec.repos == ("core", "api", "web")
    assert spec.event_kinds == (EventKind.PR, EventKind.REVIEW)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(org="not an org"), "organization"),
        (dict(org="acme", repos=("other/core",)), "does not belong"),
        (dict(org="acme", repos=("bad name",)), "Invalid repository"),
        (dict(org="acme", event_kinds=("deploy",)), "Invalid event kind"),
        (dict(org="acme", repos=tuple(f"r{i}" for i in range(41))), "at most"),
    ],
)
def test_invalid_specs_cannot_be_built(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        FeedSpec(**kwargs)


def test_feed_carries_its_spec_and_marks_seen_forward_only():
    feed = GithubFeed.new(name=" Core ", org="acme", repos=["acme/core"], event_kinds=["pr"])
    assert feed.name == "Core" and feed.repos == ["core"]
    assert feed.spec == FeedSpec("acme", repos=("core",), event_kinds=(EventKind.PR,))
    assert feed.last_seen_at is not None and feed.last_seen_at <= utc_now()

    later = utc_now() + timedelta(days=1)
    assert feed.mark_seen(later) is True
    assert feed.mark_seen(ts("2000-01-01T00:00:00Z")) is False
    assert feed.last_seen_at == later

    with pytest.raises(ValidationError, match="Name"):
        GithubFeed.new(name=" ", org="acme")
    with pytest.raises(ValidationError):
        feed.update(name="Core", org="acme", repos=["other/x"])
    assert feed.repos == ["core"]  # nothing changed
