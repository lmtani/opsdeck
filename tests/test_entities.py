"""Entities enforce their own invariants: invalid ones cannot be built."""

from datetime import timedelta

import pytest

from app.domain.common import utc_now
from app.domain.errors import ValidationError
from app.domain.github import EventKind, EventSubject, FeedSpec, GithubFeed, SubjectType
from app.domain.links import Link, LinkKind
from app.domain.logs import Freshness, LogFilter, LogQuery, LogSource, LogSourceKind, Refinement
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
    log_filter = LogFilter.new(name=" Erros ", source_id=" s1 ", expression=" e ", freshness="6h", limit=25)
    assert (log_filter.name, log_filter.source_id, log_filter.freshness) == ("Erros", "s1", Freshness.H6)
    assert log_filter.query() == LogQuery(expression="e", freshness=Freshness.H6, limit=25)

    for fields in [
        dict(name=" ", source_id="s1"),
        dict(name="n", source_id=" "),
        dict(name="n", source_id="s1", freshness="2h"),
        dict(name="n", source_id="s1", limit=0),
        dict(name="n" * 81, source_id="s1"),
    ]:
        with pytest.raises(ValidationError):
            LogFilter.new(**fields)


def test_log_query_normalizes_validates_and_stacks_refinements():
    query = LogQuery(expression=" x ", freshness="1d")
    assert (query.expression, query.freshness, query.refinements) == ("x", Freshness.D1, ())
    with pytest.raises(ValidationError):
        LogQuery(limit=501)

    first = Refinement(path=["severity"], value="ERROR")
    second = Refinement(path=("labels", "k8s-pod/app"), value="api", negate=True)
    refined = query.refined([first]).refined([second])
    assert refined.refinements == (first, second) and query.refinements == ()
    assert first.path == ("severity",)  # lists become tuples, so refinements compare by value


@pytest.mark.parametrize(("path", "value"), [((), "x"), (("a", " "), "x"), (("a",), None), (("a",), [1])])
def test_refinement_needs_a_field_and_a_scalar_value(path, value):
    with pytest.raises(ValidationError):
        Refinement(path=path, value=value)


def test_log_source_invariants():
    source = LogSource.new(name=" Prod ", kind="gcp", config={"project": " p ", "extra": "dropped"})
    assert (source.name, source.kind, source.config) == ("Prod", LogSourceKind.GCP, {"project": "p"})
    assert source.same_place("gcp", {"project": "p "}) and not source.same_place("gcp", {"project": "q"})
    assert not source.same_place("splunk", {"project": "p"})

    with pytest.raises(ValidationError, match="Unknown setting"):
        LogSource(id="x", name="n", kind=LogSourceKind.GCP, config={"project": "p", "zone": "z"})
    with pytest.raises(ValidationError, match="Project is required"):
        LogSource.new(name="n", kind="gcp", config={"project": 42})
    with pytest.raises(ValidationError, match="Unknown kind"):
        LogSource.new(name="n", kind="aws", config={})

    source.update(name=" Production ", config={"project": "p2"})
    assert (source.name, source.config, source.kind) == ("Production", {"project": "p2"}, LogSourceKind.GCP)
    with pytest.raises(ValidationError):
        source.update(name=" ", config={"project": "p3"})
    assert (source.name, source.config) == ("Production", {"project": "p2"})


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


def test_event_subject_needs_a_valid_repo_and_the_ids_of_its_type():
    review = EventSubject("review", "acme/core", number=12, item_id=7)
    assert review.type is SubjectType.REVIEW
    assert EventSubject(SubjectType.ISSUE_COMMENT, "acme/core.js", item_id=3).item_id == 3
    with pytest.raises(ValidationError, match="repository"):
        EventSubject(SubjectType.PULL, "acme", number=1)
    with pytest.raises(ValidationError, match="repository"):
        EventSubject(SubjectType.PULL, "acme/..", number=1)  # could walk up the API path
    with pytest.raises(ValidationError, match="item_id"):
        EventSubject(SubjectType.REVIEW, "acme/core", number=12)
    with pytest.raises(ValidationError, match="number"):
        EventSubject(SubjectType.PULL, "acme/core", item_id=5)
    with pytest.raises(ValueError, match="wiki"):
        EventSubject("wiki", "acme/core", item_id=5)
