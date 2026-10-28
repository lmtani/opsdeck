"""Pure GitHub rules: feed spec, burst folding, coverage, real-time merge,
daily contributions."""

from datetime import UTC, date
from zoneinfo import ZoneInfo

from app.domain.github import Contribution, ContributionKind, FeedSpec
from app.domain.github_rules import (
    collapse_bursts,
    contribution_kinds,
    contributions_on,
    coverage_start,
    daily_contributions,
    merge_realtime,
)
from tests.fakes import (
    make_batch,
    make_code_comment,
    make_event,
    make_pr_action,
    make_push,
    make_review,
    ts,
)


def test_selecting_every_kind_means_no_filter():
    spec = FeedSpec(
        "acme", event_kinds=("push", "pr", "review", "comment", "issue", "branch", "release", "other")
    )
    assert spec.event_kinds == ()


def test_consecutive_pushes_collapse_per_repo_actor_and_branch():
    events = [
        make_push(3, "2026-10-22T12:00:00Z", before="b2", head="h3"),
        make_push(9, "2026-10-22T11:30:00Z", repo="acme/api"),  # other repo: does not break the run
        make_push(2, "2026-10-22T11:00:00Z", before="b1", head="h2"),
        make_push(1, "2026-10-22T10:00:00Z", before="b0", head="h1"),
        make_event(5, "pr", at="2026-10-22T09:30:00Z"),
        make_push(0, "2026-10-22T09:00:00Z"),  # after a PR in the same repo: kept apart
        make_push(7, "2026-10-22T08:00:00Z", ref="dev"),
    ]
    result = collapse_bursts(events)
    assert [e.id for e in result] == ["3", "9", "5", "0", "7"]
    folded = result[0]
    assert folded.count == 3
    assert folded.summary == "pushed 3 times to main"
    assert folded.details.heads == ("h3", "h2", "h1")
    assert folded.url == "https://github.com/acme/core/compare/b0...h3"


def test_code_review_comment_bursts_collapse_per_pr():
    events = [
        make_code_comment(3, "2026-10-22T12:00:00Z"),
        make_code_comment(2, "2026-10-22T11:59:00Z"),
        make_code_comment(1, "2026-10-22T11:58:00Z"),
        make_code_comment(0, "2026-10-22T11:57:00Z", number=14),  # other PR
        make_event(9, "comment", at="2026-10-22T11:00:00Z", number=14, type="IssueCommentEvent"),
    ]
    result = collapse_bursts(events)
    assert [e.id for e in result] == ["3", "0", "9"]
    burst = result[0]
    assert burst.count == 3
    assert burst.summary == "left 3 review comments on PR #13"
    assert [c.path for c in burst.details.comments] == ["f3.py", "f2.py", "f1.py"]
    # each folded comment can still open its own full context
    assert [c.subject.item_id for c in burst.details.comments] == [4, 3, 2]
    assert burst.url == "https://x/3"


def _api_order(*groups):
    """Concatenate timestamp groups as the API would serve them (processing order)."""
    return [at for group in groups for at in group]


def test_coverage_is_the_median_of_the_last_processed_events():
    recent = [f"2026-10-22T{hour:02d}:00:00Z" for hour in range(23, 9, -1)] * 4  # dense, newest first
    sparse = ["2026-10-21T14:00:00Z", "2026-10-21T16:00:00Z"]  # older, mostly missing
    tail = ["2026-10-21T22:30:00Z"] * 22 + ["2026-09-23T02:51:19Z"] * 4 + ["2026-10-21T22:10:00Z"] * 4
    batch = make_batch(_api_order(recent, sparse, tail))
    # not the oldest event (September) nor the sparse 21/10 afternoon
    assert coverage_start([batch]) == ts("2026-10-21T22:30:00Z")
    complete = make_batch(_api_order(recent, tail), truncated=False)
    assert coverage_start([complete]) is None  # read to its end: no gap
    assert coverage_start([]) is None


def test_coverage_of_several_sources_is_the_latest_cut():
    busy = make_batch(["2026-10-22T20:00:00Z"] * 10 + ["2026-10-22T12:00:00Z"] * 30)
    calmer = make_batch(["2026-10-22T20:00:00Z"] * 10 + ["2026-10-20T10:00:00Z"] * 30)
    quiet = make_batch(["2026-09-01T10:00:00Z"], truncated=False)
    assert coverage_start([busy, calmer, quiet]) == ts("2026-10-22T12:00:00Z")


def test_searched_events_fill_the_gap_until_the_published_ones_arrive():
    searched = [
        make_pr_action("s1", "merged", source="search"),
        make_review("s2", "approved", source="search"),
        make_pr_action("s3", "opened", actor="dave", at="2026-10-23T09:00:00Z", source="search"),
    ]
    # nothing published yet: everything searched is kept
    assert [e.id for e in merge_realtime([], searched)] == ["s1", "s2", "s3"]
    # the merge was published (a few seconds apart): only its searched twin goes
    published = [make_pr_action("p1", "merged", at="2026-10-23T12:12:19Z")]
    assert [e.id for e in merge_realtime(published, searched)] == ["s2", "s3"]
    # same verb by someone else, or far apart in time, is a different action
    other = [
        make_review("p2", "approved", actor="erin"),
        make_review("p3", "approved", at="2026-10-22T10:00:00Z"),
    ]
    assert [e.id for e in merge_realtime(other, searched)] == ["s1", "s2", "s3"]


# ----- contributors view -----------------------------------------------------


def _contribution(login, kind, at, repo="acme/core"):
    return Contribution(login, ContributionKind(kind), repo, ts(at))


def test_daily_contributions_count_per_local_day_kind_and_repo():
    zone = ZoneInfo("America/Sao_Paulo")  # UTC-3
    items = [
        _contribution("bob", "commit", "2026-10-22T02:00:00Z"),  # still Oct 21 in São Paulo
        _contribution("bob", "commit", "2026-10-22T12:00:00Z"),
        _contribution("bob", "commit", "2026-10-22T13:00:00Z"),
        _contribution("bob", "commit", "2026-10-22T14:00:00Z", repo="acme/api"),
        _contribution("ana", "review", "2026-10-22T15:00:00Z"),
        _contribution("ana", "review", "2026-10-30T15:00:00Z"),  # after `until`
    ]
    counts = daily_contributions(items, FeedSpec("acme"), zone, date(2026, 10, 21), date(2026, 10, 22))
    assert [(c.login, str(c.day), c.kind.value, c.repo, c.count) for c in counts] == [
        ("ana", "2026-10-22", "review", "acme/core", 1),
        ("bob", "2026-10-21", "commit", "acme/core", 1),
        ("bob", "2026-10-22", "commit", "acme/core", 2),
        ("bob", "2026-10-22", "commit", "acme/api", 1),
    ]


def test_daily_contributions_follow_the_feed_kinds_and_bot_filter():
    items = [
        _contribution("bob", "commit", "2026-10-22T12:00:00Z"),
        _contribution("bob", "pr", "2026-10-22T12:00:00Z"),
        _contribution("bob", "review", "2026-10-22T12:00:00Z"),
        _contribution("dependabot[bot]", "pr", "2026-10-22T12:00:00Z"),
    ]
    day = date(2026, 10, 22)
    narrowed = FeedSpec("acme", event_kinds=("push", "pr", "comment"))
    assert {(c.login, c.kind.value) for c in daily_contributions(items, narrowed, UTC, day, day)} == {
        ("bob", "commit"),
        ("bob", "pr"),
    }
    with_bots = FeedSpec("acme", hide_bots=False)
    assert len(daily_contributions(items, with_bots, UTC, day, day)) == 4


def test_feed_kinds_without_a_contribution_counterpart_leave_nothing():
    assert contribution_kinds(FeedSpec("acme", event_kinds=("comment", "release"))) == set()
    assert contribution_kinds(FeedSpec("acme")) == set(ContributionKind)


def test_contributions_on_one_local_day_newest_first():
    zone = ZoneInfo("America/Sao_Paulo")
    items = [
        _contribution("bob", "commit", "2026-10-22T12:00:00Z"),
        _contribution("bob", "pr", "2026-10-22T18:00:00Z"),
        _contribution("Bob", "review", "2026-10-23T02:00:00Z"),  # Oct 22, 23:00 in São Paulo
        _contribution("bob", "commit", "2026-10-23T12:00:00Z"),  # next day
        _contribution("ana", "commit", "2026-10-22T12:00:00Z"),
    ]
    picked = contributions_on(items, FeedSpec("acme"), zone, "bob", date(2026, 10, 22))
    assert [c.kind.value for c in picked] == ["review", "pr", "commit"]
    narrowed = FeedSpec("acme", event_kinds=("push",))
    assert [c.kind.value for c in contributions_on(items, narrowed, zone, "bob", date(2026, 10, 22))] == [
        "commit"
    ]


def test_reviews_of_one_pull_request_count_once_per_day():
    def review(at, number, login="ana"):
        return Contribution(login, ContributionKind.REVIEW, "acme/core", ts(at), number=number)

    items = [
        review("2026-10-22T10:00:00Z", 7),
        review("2026-10-22T11:00:00Z", 7),  # a reply in the same review thread
        review("2026-10-22T12:00:00Z", 7),
        review("2026-10-22T12:30:00Z", 8),
        review("2026-10-23T09:00:00Z", 7),  # the next day counts again
        review("2026-10-22T13:00:00Z", 7, login="bob"),
    ]
    counts = daily_contributions(items, FeedSpec("acme"), UTC, date(2026, 10, 22), date(2026, 10, 23))
    assert [(c.login, str(c.day), c.count) for c in counts] == [
        ("ana", "2026-10-22", 2),
        ("ana", "2026-10-23", 1),
        ("bob", "2026-10-22", 1),
    ]
