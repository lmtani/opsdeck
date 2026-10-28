"""GitHub feed use cases with in-memory fakes — no network."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.application.github import GithubActivityService, GithubFeedService
from app.domain.common import utc_now
from app.domain.errors import GithubError, GithubNotFoundError, NotFoundError, ValidationError
from app.domain.github import (
    Contribution,
    ContributionKind,
    EventContext,
    EventSubject,
    FeedSpec,
    GithubFeed,
    ItemState,
    PullRequestInfo,
    SubjectType,
)
from tests.fakes import (
    FakeGithubProvider,
    InMemoryFeedRepository,
    make_event,
    make_pr_action,
    make_review,
    ts,
)


def test_create_normalizes_repos_and_starts_caught_up(feeds):
    feed = GithubFeedService(feeds).create_feed(
        name=" Core ",
        org="acme",
        repos=["core", "acme/api", "https://github.com/acme/web.git", "CORE", ""],
        event_kinds=["review", "pr"],
    )
    assert feed.name == "Core"
    assert feed.repos == ["core", "api", "web"]
    assert feed.event_kinds == ["pr", "review"]  # canonical order
    assert feed.last_seen_at is not None and feed.created_at is not None
    assert feeds.get(feed.id) == feed


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(org="not an org"), "organization"),
        (dict(org="acme", repos=["other/core"]), "does not belong"),
        (dict(org="acme", repos=["bad name"]), "Invalid repository"),
        (dict(org="acme", event_kinds=["deploy"]), "Invalid event kind"),
    ],
)
def test_invalid_feeds_are_rejected(feeds, kwargs, message):
    with pytest.raises(ValidationError, match=message):
        GithubFeedService(feeds).create_feed(name="x", **kwargs)


def test_mark_seen_only_moves_forward(feeds):
    service = GithubFeedService(feeds)
    feed = service.create_feed(name="x", org="acme")
    service.mark_seen(feed.id, ts("2099-01-01T00:00:00Z"))
    service.mark_seen(feed.id, ts("2000-01-01T00:00:00Z"))
    assert feeds.get(feed.id).last_seen_at == ts("2099-01-01T00:00:00Z")
    with pytest.raises(NotFoundError):
        service.mark_seen("missing")


def test_org_feed_sorts_dedupes_and_filters(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [
        make_event(1, "pr", at="2026-10-22T09:00:00Z"),
        make_event(2, "review", actor="dependabot[bot]", at="2026-10-22T11:00:00Z"),
        make_event(3, "comment", actor="Alice", at="2026-10-22T12:00:00Z"),
        make_event(4, "branch", at="2026-10-22T10:00:00Z"),
        make_event(1, "pr", at="2026-10-22T09:00:00Z"),
    ]
    spec = FeedSpec("acme", event_kinds=["pr", "review", "comment"], hide_own=True)
    activity = GithubActivityService(provider, feeds).run_query(spec)
    assert [e.id for e in activity.events] == ["1"]  # bot, own and branch dropped
    assert activity.viewer == "alice"


def test_repo_feed_merges_repos_and_reports_partial_failures(feeds):
    provider = FakeGithubProvider()
    provider.repos[("acme", "core")] = [make_event(1, "pr", at="2026-10-22T09:00:00Z")]
    provider.repos[("acme", "api")] = [make_event(2, "pr", repo="acme/api", at="2026-10-22T10:00:00Z")]
    provider.repos[("acme", "gone")] = GithubNotFoundError("404")
    spec = FeedSpec("acme", repos=["core", "api", "gone"])
    activity = GithubActivityService(provider, feeds).run_query(spec)
    assert [e.id for e in activity.events] == ["2", "1"]
    assert activity.warnings == ["acme/gone does not exist or the token cannot see it."]


def test_repo_feed_fails_when_every_repo_fails(feeds):
    provider = FakeGithubProvider()
    provider.repos[("acme", "core")] = GithubError("GitHub token is invalid or expired")
    with pytest.raises(GithubError, match="token"):
        GithubActivityService(provider, feeds).run_query(FeedSpec("acme", repos=["core"]))


def test_pull_request_titles_and_states_are_filled_in(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [
        make_event(1, "pr", number=7, is_pull=True, action="opened"),
        make_event(2, "comment", number=8, title="Issue title", is_pull=False),
    ]
    provider.prs[("acme/core", 7)] = PullRequestInfo(title="Fix login", state=ItemState.MERGED)
    activity = GithubActivityService(provider, feeds).run_query(FeedSpec("acme"))
    by_id = {e.id: e for e in activity.events}
    assert (by_id["1"].title, by_id["1"].state) == ("Fix login", "merged")
    assert by_id["2"].title == "Issue title"
    assert provider.pr_calls == [[("acme/core", 7)]]  # issues are not looked up


def test_graphql_failure_degrades_to_a_warning(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [make_event(1, "pr", number=7, is_pull=True)]

    def boom(refs):
        raise GithubError("GraphQL down")

    provider.pull_requests = boom
    activity = GithubActivityService(provider, feeds).run_query(FeedSpec("acme"))
    assert len(activity.events) == 1
    assert activity.warnings == ["PR titles unavailable: GraphQL down"]


def test_unseen_counts_per_feed(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [
        make_event(1, "pr", at="2026-10-22T09:00:00Z"),
        make_event(2, "pr", at="2026-10-22T11:00:00Z"),
        make_event(3, "pr", at="2026-10-22T12:00:00Z"),
    ]
    provider.repos[("acme", "core")] = GithubError("rate limited")
    feeds.save(GithubFeed(id="a", name="org", org="acme", last_seen_at=ts("2026-10-22T10:00:00Z")))
    feeds.save(GithubFeed(id="b", name="never", org="acme", last_seen_at=None))
    feeds.save(GithubFeed(id="c", name="broken", org="acme", repos=["core"]))
    counts = GithubActivityService(provider, feeds).unseen_counts()
    assert counts == {"a": 2, "b": 0}
    assert provider.pr_calls == []  # counting skips the GraphQL enrichment


def test_activity_reports_coverage_of_a_truncated_org(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [make_event(i, at=f"2026-10-22T{14 - i:02d}:00:00Z") for i in range(5)]
    provider.truncated.add("acme")
    activity = GithubActivityService(provider, feeds).run_query(FeedSpec("acme"))
    assert activity.coverage_start == ts("2026-10-22T12:00:00Z")  # median of a short tail


def test_feed_merges_searched_pull_request_activity(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [make_event(1, "push", at="2026-10-23T08:00:00Z")]
    provider.searched = [make_pr_action("s1", "merged", source="search")]
    activity = GithubActivityService(provider, feeds).run_query(FeedSpec("acme", repos=[]))
    assert [e.id for e in activity.events] == ["s1", "1"]
    spec, since = provider.activity_calls[0]
    assert (spec.org, spec.repos) == ("acme", ()) and since.tzinfo is not None


def test_search_follows_the_feed_filters_and_failures_become_warnings(feeds):
    provider = FakeGithubProvider()
    provider.repos[("acme", "dockerfiles")] = []
    provider.searched = [
        make_pr_action("s1", "merged", source="search"),
        make_review("s2", "approved", actor="renovate[bot]", source="search"),
    ]
    spec = FeedSpec("acme", repos=["dockerfiles"], event_kinds=["review"])
    activity = GithubActivityService(provider, feeds).run_query(spec)
    assert activity.events == []  # merge is not a review, the review is a bot's
    assert provider.activity_calls[0][0].repos == ("dockerfiles",)

    provider.searched = GithubError("GraphQL down")
    activity = GithubActivityService(provider, feeds).run_query(FeedSpec("acme"))
    assert activity.warnings == ["Real-time PR search unavailable: GraphQL down"]


@pytest.fixture
def feeds():
    return InMemoryFeedRepository()


def test_update_feed_revalidates_and_keeps_mark_and_identity(feeds):
    service = GithubFeedService(feeds)
    created = service.create_feed(name="x", org="acme", repos=["core"])
    service.mark_seen(created.id, ts("2099-01-01T00:00:00Z"))

    updated = service.update_feed(
        created.id,
        name=" y ",
        org="acme",
        repos=["acme/api", "web"],
        event_kinds=["push"],
        hide_own=True,
        hide_bots=False,
        description=" d ",
    )
    assert updated.id == created.id and updated.created_at == created.created_at
    assert updated.last_seen_at == ts("2099-01-01T00:00:00Z")  # editing does not reset "seen"
    assert (updated.name, updated.repos, updated.event_kinds) == ("y", ["api", "web"], ["push"])
    assert (updated.hide_own, updated.hide_bots, updated.description) == (True, False, "d")

    with pytest.raises(ValidationError):
        service.update_feed(created.id, name="y", org="acme", repos=["other/api"])
    with pytest.raises(NotFoundError):
        service.update_feed("ghost", name="y", org="acme")


def test_delete_and_reorder_feeds(feeds):
    service = GithubFeedService(feeds)
    first = service.create_feed(name="a", org="acme")
    second = service.create_feed(name="b", org="acme")
    third = service.create_feed(name="c", org="acme")

    service.reorder_feeds([second.id, third.id, first.id])
    assert [feed.id for feed in service.list_feeds()] == [second.id, third.id, first.id]

    service.delete_feed(third.id)
    assert [feed.id for feed in service.list_feeds()] == [second.id, first.id]
    with pytest.raises(NotFoundError):
        service.delete_feed(third.id)


def test_list_org_repos_validates_the_org(feeds):
    service = GithubActivityService(FakeGithubProvider(), feeds)
    assert service.list_org_repos(" acme ") == []
    with pytest.raises(ValidationError):
        service.list_org_repos("not an org")


def test_run_feed_flags_events_newer_than_the_mark(feeds):
    provider = FakeGithubProvider()
    provider.org["acme"] = [
        make_event(1, "pr", at="2026-10-22T09:00:00Z"),
        make_event(2, "pr", at="2026-10-22T11:00:00Z"),
    ]
    feeds.save(GithubFeed(id="a", name="org", org="acme", last_seen_at=ts("2026-10-22T10:00:00Z")))
    activity = GithubActivityService(provider, feeds).run_feed("a")
    assert [(e.id, e.is_new) for e in activity.events] == [("2", True), ("1", False)]
    assert activity.unseen == 1

    ad_hoc = GithubActivityService(provider, feeds).run_query(FeedSpec("acme"))
    assert ad_hoc.unseen == 0 and not any(e.is_new for e in ad_hoc.events)


def test_event_context_comes_from_the_provider_and_a_missing_item_is_not_found(feeds):
    provider = FakeGithubProvider()
    service = GithubActivityService(provider, feeds)
    subject = EventSubject(SubjectType.ISSUE_COMMENT, "acme/core", item_id=3)
    provider.contexts[subject] = EventContext(subject=subject, body_html="<p>full text</p>")
    assert service.event_context(subject).body_html == "<p>full text</p>"
    with pytest.raises(NotFoundError, match="no longer exists"):
        service.event_context(EventSubject(SubjectType.ISSUE_COMMENT, "acme/core", item_id=4))
    provider.contexts[subject] = GithubError("GitHub rate limit exhausted")
    with pytest.raises(GithubError):
        service.event_context(subject)


# ----- contributors view -----------------------------------------------------


def test_feed_contributions_read_local_days_since_midnight(feeds):
    provider = FakeGithubProvider()
    feed = GithubFeedService(feeds).create_feed(name="Org", org="acme", hide_bots=True)
    now = utc_now()
    provider.contributed = [
        Contribution("bob", ContributionKind.COMMIT, "acme/core", now, "https://a/bob"),
        Contribution("bob", ContributionKind.REVIEW, "acme/core", now),
        Contribution("renovate[bot]", ContributionKind.PR, "acme/core", now),
    ]
    provider.contribution_warnings = ["3 commits left out"]

    result = GithubActivityService(provider, feeds).feed_contributions(feed.id, 14, "America/Sao_Paulo")

    zone = ZoneInfo("America/Sao_Paulo")
    assert result.until == now.astimezone(zone).date()
    assert result.since == result.until - timedelta(days=13)
    ((spec, since),) = provider.contribution_calls
    assert spec == feed.spec
    assert since == datetime.combine(result.since, time.min, tzinfo=zone)  # local midnight
    assert [(c.login, c.kind.value, c.count) for c in result.counts] == [
        ("bob", "commit", 1),
        ("bob", "review", 1),
    ]
    assert result.avatars == {"bob": "https://a/bob"}
    assert result.warnings == ["3 commits left out"] and result.viewer == "alice"


@pytest.mark.parametrize(("days", "zone"), [(0, "UTC"), (93, "UTC"), (28, "Mars/Olympus"), (28, "../etc")])
def test_feed_contributions_reject_bad_periods_and_zones(feeds, days, zone):
    feed = GithubFeedService(feeds).create_feed(name="Org", org="acme")
    with pytest.raises(ValidationError):
        GithubActivityService(FakeGithubProvider(), feeds).feed_contributions(feed.id, days, zone)


def test_feed_contributions_of_an_unknown_feed(feeds):
    with pytest.raises(NotFoundError):
        GithubActivityService(FakeGithubProvider(), feeds).feed_contributions("ghost", 28, "UTC")


def test_contribution_day_lists_one_cell_from_the_same_read(feeds):
    provider = FakeGithubProvider()
    feed = GithubFeedService(feeds).create_feed(name="Org", org="acme")
    now = utc_now()
    provider.contributed = [
        Contribution("bob", ContributionKind.COMMIT, "acme/core", now, title="fix: it"),
        Contribution("ana", ContributionKind.COMMIT, "acme/core", now),
    ]
    service = GithubActivityService(provider, feeds)
    grid = service.feed_contributions(feed.id, 7, "UTC")

    items = service.contribution_day(feed.id, 7, "UTC", "bob", grid.until)
    assert [(c.login, c.title) for c in items] == [("bob", "fix: it")]
    assert provider.contribution_calls[0] == provider.contribution_calls[1]  # same window, same cache key
    with pytest.raises(ValidationError):
        service.contribution_day(feed.id, 7, "UTC", "bob", grid.since - timedelta(days=1))
