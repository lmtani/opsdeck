"""The REST objects behind an event's subject, read with the html media
type, as an EventContext: body, thread, pull request stats. Pure functions;
body_html is sanitized here."""

import re

from app.domain.common import EPOCH, parse_timestamp
from app.domain.github import (
    EventContext,
    EventSubject,
    Label,
    PullStats,
    Reviewer,
    ReviewState,
    SubjectType,
    ThreadComment,
)
from app.infrastructure.github.html_sanitizer import sanitize_html
from app.infrastructure.github.parsing.common import html_url, item_state, pull_state, review_state


def context_from_api(subject: EventSubject, data: dict) -> EventContext:
    """The REST object behind a subject, read with the html media type, as
    an EventContext. Its body_html is sanitized here."""
    user = data.get("user") or data.get("author") or {}
    when = data.get("submitted_at") or data.get("published_at") or data.get("created_at")
    if subject.type == SubjectType.PULL:
        state = pull_state(data)
    elif subject.type == SubjectType.ISSUE:
        state = item_state(data.get("state"))
    else:
        state = None
    is_review = subject.type == SubjectType.REVIEW
    return EventContext(
        subject=subject,
        url=html_url(data),
        author=user.get("login") or "",
        author_avatar=user.get("avatar_url") or "",
        created_at=parse_timestamp(when),
        title=data.get("title") or data.get("name") or "",
        state=state,
        review_state=review_state(data.get("state")) if is_review else None,
        body_html=sanitize_html(data.get("body_html") or ""),
        path=data.get("path") or "",
        line=data.get("line") or data.get("original_line"),
        diff_hunk=data.get("diff_hunk") or "",
    )


def thread_comment_from_api(data: dict) -> ThreadComment:
    user = data.get("user") or {}
    return ThreadComment(
        id=int(data.get("id") or 0),
        author=user.get("login") or "ghost",
        author_avatar=user.get("avatar_url") or "",
        created_at=parse_timestamp(data.get("created_at")),
        body_html=sanitize_html(data.get("body_html") or ""),
        url=html_url(data),
        path=data.get("path") or "",
        line=data.get("line") or data.get("original_line"),
        diff_hunk=data.get("diff_hunk") or "",
    )


def review_thread(root: dict, comments: list[dict]) -> tuple[ThreadComment, ...]:
    """A review thread, oldest first: its root comment and the replies among
    `comments` (GitHub points every reply at the root, not at its parent)."""
    root_id = root.get("id")
    by_id = {root_id: root}
    for comment in comments:
        if comment.get("in_reply_to_id") == root_id:
            by_id.setdefault(comment.get("id"), comment)
    ordered = sorted(by_id.values(), key=lambda c: parse_timestamp(c.get("created_at")) or EPOCH)
    return tuple(thread_comment_from_api(comment) for comment in ordered)


def pull_number(pull_request_url: str | None) -> int:
    """The number at the end of a REST pull request URL (…/pulls/26), or 0."""
    tail = (pull_request_url or "").rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else 0


_HEX_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


def pull_stats_from_api(pull: dict, reviews: list[dict]) -> PullStats:
    """Size, branches, labels and reviewers of a REST pull request; `reviews`
    in the order GitHub lists them (oldest first)."""
    author = (pull.get("user") or {}).get("login")
    latest: dict[str, ReviewState] = {}
    for review in reviews:
        login = (review.get("user") or {}).get("login")
        state = review_state(review.get("state"))
        if not login or login == author or state is None:
            continue
        if state == ReviewState.COMMENTED and login in latest:
            continue  # a later comment does not undo an approval or a change request
        latest[login] = state
    requested = [user.get("login") for user in pull.get("requested_reviewers") or []]
    requested += [team.get("slug") for team in pull.get("requested_teams") or []]
    requested = [login for login in requested if login]
    # re-requested after reviewing: their old verdict no longer counts
    reviewers = [Reviewer(login, state) for login, state in latest.items() if login not in requested]
    reviewers += [Reviewer(login) for login in requested]
    labels = tuple(
        Label(label["name"], color if _HEX_COLOR.match(color := label.get("color") or "") else "")
        for label in pull.get("labels") or []
        if label.get("name")
    )
    return PullStats(
        additions=int(pull.get("additions") or 0),
        deletions=int(pull.get("deletions") or 0),
        changed_files=int(pull.get("changed_files") or 0),
        commits=int(pull.get("commits") or 0),
        head_ref=(pull.get("head") or {}).get("ref") or "",
        base_ref=(pull.get("base") or {}).get("ref") or "",
        labels=labels,
        reviewers=tuple(reviewers),
    )
