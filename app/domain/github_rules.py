"""Pure GitHub rules: no I/O, no clock.

Conventions shared by the event parser and the use cases (URLs, wording,
bot logins), plus the timeline logic the use cases orchestrate: the feed's
filters, "new since last seen", burst folding, coverage of the events API
and the merge of real-time search results; and the daily buckets of the
contributors view.
"""

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, tzinfo

from app.domain.github import (
    CodeComment,
    CodeCommentDetails,
    Contribution,
    ContributionKind,
    DailyContribution,
    EventKind,
    FeedSpec,
    GithubEvent,
    GithubEventBatch,
    PushDetails,
    ReviewDetails,
)

GITHUB_WEB = "https://github.com"
ZERO_SHA = "0" * 40
# The events API serves a source's last ~300 events in processing order, and
# processing can lag creation by hours or weeks: older events are scattered
# through the pages and the oldest days look calm while most of their events
# are missing. The events processed last (the tail, in API order) sit at the
# real boundary, so their median creation time marks where coverage starts.
COVERAGE_TAIL = 30
# a searched event and a published one describe the same action when they
# match on type/PR/verb/actor and lie this close in time
REALTIME_MATCH_SECONDS = 600
REVIEW_COMMENT_TYPE = "PullRequestReviewCommentEvent"


# ----- conventions -------------------------------------------------------


def short_sha(sha: str) -> str:
    return (sha or "")[:7]


def compare_url(repo: str, before: str, head: str) -> str:
    """Link to a pushed range, or to the head commit when the branch is new."""
    if not before or before == ZERO_SHA:
        return f"{GITHUB_WEB}/{repo}/commit/{head}" if head else f"{GITHUB_WEB}/{repo}"
    return f"{GITHUB_WEB}/{repo}/compare/{short_sha(before)}...{short_sha(head)}"


def pull_url(repo: str, number: int) -> str:
    return f"{GITHUB_WEB}/{repo}/pull/{number}"


def push_summary(branch: str, count: int = 1) -> str:
    if count > 1:
        return f"pushed {count} times to {branch}"
    return f"pushed to {branch}"


def review_comments_summary(number: int, count: int = 1) -> str:
    if count > 1:
        return f"left {count} review comments on PR #{number}"
    return f"left a review comment on PR #{number}"


def is_bot(login: str) -> bool:
    return login.lower().endswith("[bot]")


# ----- timeline ----------------------------------------------------------


def is_unseen(event: GithubEvent, last_seen_at: datetime | None) -> bool:
    """Newer than the feed's last-seen mark. Without a mark nothing is new."""
    return last_seen_at is not None and event.created_at > last_seen_at


def keep_event(event: GithubEvent, spec: FeedSpec, viewer: str) -> bool:
    """Whether the spec's kind/bot/own filters let the event through."""
    if spec.event_kinds and event.kind not in spec.event_kinds:
        return False
    if spec.hide_bots and is_bot(event.actor):
        return False
    if spec.hide_own and viewer and event.actor.lower() == viewer.lower():
        return False
    return True


def _comment_item(event: GithubEvent) -> CodeComment:
    path = event.details.path if isinstance(event.details, CodeCommentDetails) else ""
    return CodeComment(url=event.url, path=path, body=event.body, subject=event.subject)


def _push_details(event: GithubEvent) -> PushDetails:
    return event.details if isinstance(event.details, PushDetails) else PushDetails()


def _fold_push(kept: GithubEvent, older: GithubEvent) -> None:
    current, previous = _push_details(kept), _push_details(older)
    kept.details = replace(current, before=previous.before, heads=current.heads + previous.heads)
    kept.summary = push_summary(kept.ref, kept.count)
    kept.url = compare_url(kept.repo, kept.details.before, kept.details.head)


def _fold_review_comment(kept: GithubEvent, older: GithubEvent) -> None:
    current = kept.details if isinstance(kept.details, CodeCommentDetails) else CodeCommentDetails()
    comments = current.comments or (_comment_item(kept),)
    kept.details = replace(current, comments=(*comments, _comment_item(older)))
    kept.summary = review_comments_summary(kept.number, kept.count)


def _same_burst(kept: GithubEvent, event: GithubEvent) -> bool:
    if kept.actor != event.actor or kept.type != event.type:
        return False
    if event.kind == EventKind.PUSH:
        return kept.ref == event.ref
    if event.type == REVIEW_COMMENT_TYPE:
        return kept.number == event.number
    return False


def collapse_bursts(events: list[GithubEvent]) -> list[GithubEvent]:
    """Fold bursts into one event: consecutive pushes of one actor to one
    branch, and consecutive code-review comments of one actor on one PR.

    `events` is newest first. "Consecutive" is judged per repo: an event
    folds into the previous kept event of the same repo when both belong to
    the same burst. The folded event keeps the newest time and URL (for
    pushes, the URL of the whole compared range) and counts the folded ones.
    """
    result: list[GithubEvent] = []
    last_by_repo: dict[str, GithubEvent] = {}
    for event in events:
        kept = last_by_repo.get(event.repo)
        if kept is not None and _same_burst(kept, event):
            kept.count += 1
            if event.kind == EventKind.PUSH:
                _fold_push(kept, event)
            else:
                _fold_review_comment(kept, event)
            continue
        result.append(event)
        last_by_repo[event.repo] = event
    return result


def coverage_start(batches: list[GithubEventBatch]) -> datetime | None:
    """Instant before which the merged timeline is incomplete, or None.

    Only truncated sources matter: a source read to its end has no gap. For
    each truncated one (events in API order) the start is the median creation
    time of its last COVERAGE_TAIL events, the ones processed last. With
    several truncated sources the latest start wins: before it, at least one
    source is missing events.
    """
    starts = []
    for batch in batches:
        if not batch.truncated or not batch.events:
            continue
        tail = sorted(event.created_at for event in batch.events[-COVERAGE_TAIL:])
        starts.append(tail[len(tail) // 2])
    return max(starts, default=None)


def _same_action_key(event: GithubEvent) -> tuple:
    verb = event.details.state if isinstance(event.details, ReviewDetails) else event.action
    return (event.type, event.repo, event.number, verb, event.actor.lower())


def merge_realtime(published: list[GithubEvent], searched: list[GithubEvent]) -> list[GithubEvent]:
    """Searched events the events API has not published yet.

    Once the published event arrives it wins (it is the canonical record);
    until then the searched one fills the gap.
    """
    times: dict[tuple, list[datetime]] = {}
    for event in published:
        times.setdefault(_same_action_key(event), []).append(event.created_at)
    missing = []
    for event in searched:
        twins = times.get(_same_action_key(event), [])
        if any(abs((event.created_at - other).total_seconds()) <= REALTIME_MATCH_SECONDS for other in twins):
            continue
        missing.append(event)
    return missing


# ----- contributors view -------------------------------------------------

# the feed kinds that have a contribution counterpart; the others (comments,
# branches, releases) are not contributions on GitHub's graph
_CONTRIBUTION_KINDS = {
    EventKind.PUSH: ContributionKind.COMMIT,
    EventKind.PR: ContributionKind.PR,
    EventKind.REVIEW: ContributionKind.REVIEW,
    EventKind.ISSUE: ContributionKind.ISSUE,
}


def contribution_kinds(spec: FeedSpec) -> set[ContributionKind]:
    """The contribution kinds a feed's event kinds stand for (all when the
    feed is not narrowed)."""
    if not spec.event_kinds:
        return set(ContributionKind)
    return {_CONTRIBUTION_KINDS[kind] for kind in spec.event_kinds if kind in _CONTRIBUTION_KINDS}


def _kept_contributions(contributions: list[Contribution], spec: FeedSpec):
    """The contributions the feed's kinds and bot filter let through.
    hide_own is ignored: the view is about everyone's volume."""
    kinds = contribution_kinds(spec)
    return (
        item for item in contributions if item.kind in kinds and not (spec.hide_bots and is_bot(item.login))
    )


def daily_contributions(
    contributions: list[Contribution], spec: FeedSpec, zone: tzinfo, since: date, until: date
) -> list[DailyContribution]:
    """Contributions counted per person, local day, kind and repo, keeping
    the days in [since, until] that the feed lets through.

    A person's reviews of one pull request count once per day: every reply
    in a review thread is a "commented" review of its own, so counting them
    one by one ranks whoever discusses most over whoever reviews most."""
    counts: Counter[tuple[str, date, ContributionKind, str]] = Counter()
    reviewed: set[tuple[str, date, str, int]] = set()
    for item in _kept_contributions(contributions, spec):
        day = item.at.astimezone(zone).date()
        if not since <= day <= until:
            continue
        if item.kind is ContributionKind.REVIEW and item.number:
            pull = (item.login.lower(), day, item.repo, item.number)
            if pull in reviewed:
                continue
            reviewed.add(pull)
        counts[(item.login, day, item.kind, item.repo)] += 1
    return [
        DailyContribution(login=login, day=day, kind=kind, repo=repo, count=count)
        for (login, day, kind, repo), count in sorted(counts.items(), key=lambda pair: pair[0][:2])
    ]


def contributions_on(
    contributions: list[Contribution], spec: FeedSpec, zone: tzinfo, login: str, day: date
) -> list[Contribution]:
    """What one person contributed on one local day, newest first: the items
    behind a cell of the contributors grid."""
    picked = [
        item
        for item in _kept_contributions(contributions, spec)
        if item.login.lower() == login.lower() and item.at.astimezone(zone).date() == day
    ]
    return sorted(picked, key=lambda item: item.at, reverse=True)
