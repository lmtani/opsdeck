"""Normalizes raw records of the GitHub events API into GithubEvent.

Pure functions, no I/O. The events API trims its payloads: PushEvent carries
only before/head/ref (no commit list) and PullRequestEvent only number, head
and base (no title nor URL). Titles and PR state are filled in later from
GraphQL by the application service. Older, fuller payloads are still read
when present.
"""

import re
from datetime import datetime

from app.domain.common import EPOCH, parse_timestamp
from app.domain.errors import ValidationError
from app.domain.github import (
    CodeCommentDetails,
    Commit,
    EventContext,
    EventKind,
    EventSource,
    EventSubject,
    GithubEvent,
    ItemState,
    Label,
    PullDetails,
    PullStats,
    PushDetails,
    RefDetails,
    ReviewDetails,
    Reviewer,
    ReviewState,
    SubjectType,
    ThreadComment,
)
from app.domain.github_rules import (
    GITHUB_WEB,
    compare_url,
    pull_url,
    push_summary,
    review_comments_summary,
    short_sha,
)
from app.infrastructure.html_sanitizer import sanitize_html

BODY_EXCERPT = 400


def ref_name(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/tags/"):
        if (ref or "").startswith(prefix):
            return ref[len(prefix) :]
    return ref or ""


def _excerpt(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= BODY_EXCERPT else text[: BODY_EXCERPT - 1] + "…"


def _subject(type_: SubjectType, repo: str, number=0, item_id=0) -> EventSubject | None:
    """None when the payload lacks an identifier: the event still shows, only
    without a full context to open."""
    try:
        return EventSubject(type_, repo, int(number or 0), int(item_id or 0))
    except (ValidationError, TypeError, ValueError):
        return None


def _html_url(obj: dict | None) -> str:
    obj = obj or {}
    return obj.get("html_url") or ((obj.get("_links") or {}).get("html") or {}).get("href", "")


def _item_state(value: str | None, draft: bool = False, merged: bool = False) -> ItemState | None:
    if merged:
        return ItemState.MERGED
    state = (value or "").lower()
    if state == "open" and draft:
        return ItemState.DRAFT
    try:
        return ItemState(state)
    except ValueError:
        return None


def _pull_state(pull: dict) -> ItemState | None:
    merged = bool(pull.get("merged") or pull.get("merged_at"))
    return _item_state(pull.get("state"), draft=bool(pull.get("draft")), merged=merged)


# ----- per-type handlers (mutate the event in place) --------------------


def _push(event: GithubEvent, payload: dict) -> None:
    event.kind = EventKind.PUSH
    event.ref = ref_name(payload.get("ref", ""))
    before, head = payload.get("before", ""), payload.get("head", "")
    commits = payload.get("commits") or []
    event.summary = push_summary(event.ref)
    event.url = compare_url(event.repo, before, head)
    event.details = PushDetails(
        before=before,
        head=head,
        heads=(head,) if head else (),
        commits=tuple(  # older payloads only
            Commit(sha=short_sha(c.get("sha", "")), message=(c.get("message") or "").splitlines()[0])
            for c in commits[:20]
        ),
    )


_PR_VERBS = {
    "opened": "opened PR",
    "closed": "closed PR",
    "merged": "merged PR",
    "reopened": "reopened PR",
    "ready_for_review": "marked ready for review PR",
    "converted_to_draft": "converted to draft PR",
    "review_requested": "requested review on PR",
    "assigned": "assigned PR",
    "edited": "edited PR",
    "synchronize": "updated PR",
}


def _pull_request(event: GithubEvent, payload: dict) -> None:
    pull = payload.get("pull_request") or {}
    event.kind = EventKind.PR
    event.number = int(payload.get("number") or pull.get("number") or 0)
    if event.action == "closed" and pull.get("merged"):
        event.action = "merged"
    verb = _PR_VERBS.get(event.action, f"{event.action or 'updated'} PR")
    event.summary = f"{verb} #{event.number}"
    event.url = pull.get("html_url") or pull_url(event.repo, event.number)
    event.title = pull.get("title") or ""
    event.state = _pull_state(pull) if pull.get("state") else None
    if event.action == "merged":
        event.state = ItemState.MERGED
    head, base = pull.get("head") or {}, pull.get("base") or {}
    event.ref = head.get("ref", "")
    event.is_pull = True
    event.details = PullDetails(head_ref=head.get("ref", ""), base_ref=base.get("ref", ""))
    event.subject = _subject(SubjectType.PULL, event.repo, event.number)
    if event.action == "opened":
        event.body = _excerpt(pull.get("body"))


_REVIEW_VERBS: dict[ReviewState | None, str] = {
    ReviewState.APPROVED: "approved PR",
    ReviewState.CHANGES_REQUESTED: "requested changes on PR",
    ReviewState.COMMENTED: "reviewed PR",
    ReviewState.DISMISSED: "dismissed a review on PR",
}


def _review_state(value: str | None) -> ReviewState | None:
    try:
        return ReviewState((value or "").lower())
    except ValueError:
        return None


def _review(event: GithubEvent, payload: dict) -> None:
    pull, review = payload.get("pull_request") or {}, payload.get("review") or {}
    event.kind = EventKind.REVIEW
    event.number = int(pull.get("number") or 0)
    state = _review_state(review.get("state"))
    event.summary = f"{_REVIEW_VERBS.get(state, 'reviewed PR')} #{event.number}"
    event.url = _html_url(review) or pull_url(event.repo, event.number)
    event.title = pull.get("title") or ""
    event.ref = (pull.get("head") or {}).get("ref", "")
    event.body = _excerpt(review.get("body"))
    event.is_pull = True
    event.details = ReviewDetails(state=state)
    event.subject = _subject(SubjectType.REVIEW, event.repo, event.number, review.get("id"))


def _review_comment(event: GithubEvent, payload: dict) -> None:
    pull, comment = payload.get("pull_request") or {}, payload.get("comment") or {}
    event.kind = EventKind.COMMENT
    event.number = int(pull.get("number") or 0)
    event.summary = review_comments_summary(event.number)
    event.url = _html_url(comment) or pull_url(event.repo, event.number)
    event.title = pull.get("title") or ""
    event.ref = (pull.get("head") or {}).get("ref", "")
    event.body = _excerpt(comment.get("body"))
    event.is_pull = True
    event.details = CodeCommentDetails(
        path=comment.get("path") or "",
        line=comment.get("line") or comment.get("original_line"),
    )
    event.subject = _subject(SubjectType.REVIEW_COMMENT, event.repo, item_id=comment.get("id"))


def _issue_comment(event: GithubEvent, payload: dict) -> None:
    issue, comment = payload.get("issue") or {}, payload.get("comment") or {}
    event.kind = EventKind.COMMENT
    event.number = int(issue.get("number") or 0)
    is_pull = bool(issue.get("pull_request"))
    target = "PR" if is_pull else "issue"
    event.summary = f"commented on {target} #{event.number}"
    event.url = _html_url(comment) or _html_url(issue)
    event.title = issue.get("title") or ""
    # an issue's "closed" may hide a merge; PR state is resolved via GraphQL
    event.state = None if is_pull else _item_state(issue.get("state"))
    event.body = _excerpt(comment.get("body"))
    event.is_pull = is_pull
    event.subject = _subject(SubjectType.ISSUE_COMMENT, event.repo, item_id=comment.get("id"))


_ISSUE_VERBS = {
    "opened": "opened issue",
    "closed": "closed issue",
    "reopened": "reopened issue",
    "assigned": "assigned issue",
    "labeled": "labeled issue",
    "edited": "edited issue",
}


def _issues(event: GithubEvent, payload: dict) -> None:
    issue = payload.get("issue") or {}
    event.kind = EventKind.ISSUE
    event.number = int(issue.get("number") or 0)
    event.summary = f"{_ISSUE_VERBS.get(event.action, 'updated issue')} #{event.number}"
    event.url = _html_url(issue) or f"{GITHUB_WEB}/{event.repo}/issues/{event.number}"
    event.title = issue.get("title") or ""
    event.state = _item_state(issue.get("state"))
    event.subject = _subject(SubjectType.ISSUE, event.repo, event.number)
    if event.action == "opened":
        event.body = _excerpt(issue.get("body"))


_REF_NOUNS = {"branch": "branch", "tag": "tag"}


def _create(event: GithubEvent, payload: dict) -> None:
    ref_type = payload.get("ref_type", "")
    event.kind = EventKind.BRANCH
    event.ref = payload.get("ref") or ""
    if ref_type == "repository":
        event.summary = "created the repository"
        event.details = RefDetails(ref_type=ref_type)
        return
    event.summary = f"created {_REF_NOUNS.get(ref_type, ref_type)} {event.ref}"
    event.url = (
        f"{GITHUB_WEB}/{event.repo}/releases/tag/{event.ref}"
        if ref_type == "tag"
        else f"{GITHUB_WEB}/{event.repo}/tree/{event.ref}"
    )
    event.details = RefDetails(ref_type=ref_type)


def _delete(event: GithubEvent, payload: dict) -> None:
    ref_type = payload.get("ref_type", "")
    event.kind = EventKind.BRANCH
    event.ref = payload.get("ref") or ""
    event.summary = f"deleted {_REF_NOUNS.get(ref_type, ref_type)} {event.ref}"
    event.url = f"{GITHUB_WEB}/{event.repo}/{'tags' if ref_type == 'tag' else 'branches'}"
    event.details = RefDetails(ref_type=ref_type)


def _release(event: GithubEvent, payload: dict) -> None:
    release = payload.get("release") or {}
    event.kind = EventKind.RELEASE
    event.ref = release.get("tag_name", "")
    verb = "published release" if event.action in ("published", "released", "") else f"{event.action} release"
    event.summary = f"{verb} {event.ref}".strip()
    event.title = release.get("name") or ""
    event.url = _html_url(release) or f"{GITHUB_WEB}/{event.repo}/releases"
    event.body = _excerpt(release.get("body"))
    event.subject = _subject(SubjectType.RELEASE, event.repo, item_id=release.get("id"))


_SIMPLE = {
    "ForkEvent": "forked the repository",
    "WatchEvent": "starred the repository",
    "PublicEvent": "made the repository public",
    "GollumEvent": "edited the wiki",
    "CommitCommentEvent": "commented on a commit",
    "PullRequestReviewThreadEvent": "resolved a review thread",
}


def _other(event: GithubEvent, payload: dict) -> None:
    event.kind = EventKind.OTHER
    if event.type == "MemberEvent":
        member = (payload.get("member") or {}).get("login", "")
        event.summary = f"added {member} as a collaborator".strip()
        return
    if event.type == "ForkEvent":
        event.url = _html_url(payload.get("forkee")) or event.url
    if event.type == "CommitCommentEvent":
        event.url = _html_url(payload.get("comment")) or event.url
        comment = payload.get("comment") or {}
        event.body = _excerpt(comment.get("body"))
        event.subject = _subject(SubjectType.COMMIT_COMMENT, event.repo, item_id=comment.get("id"))
    event.summary = _SIMPLE.get(event.type, event.type.removesuffix("Event") or "evento")


_HANDLERS = {
    "PushEvent": _push,
    "PullRequestEvent": _pull_request,
    "PullRequestReviewEvent": _review,
    "PullRequestReviewCommentEvent": _review_comment,
    "IssueCommentEvent": _issue_comment,
    "IssuesEvent": _issues,
    "CreateEvent": _create,
    "DeleteEvent": _delete,
    "ReleaseEvent": _release,
}


def parse_event(raw: dict) -> GithubEvent:
    payload = raw.get("payload") or {}
    actor = raw.get("actor") or {}
    repo = (raw.get("repo") or {}).get("name", "")
    event = GithubEvent(
        id=str(raw.get("id", "")),
        kind=EventKind.OTHER,
        type=raw.get("type", ""),
        repo=repo,
        # login, not display_login: bots are "dependabot[bot]" only in login,
        # and it is what is_bot() and the hide_own comparison rely on
        actor=actor.get("login") or actor.get("display_login") or "",
        created_at=parse_timestamp(raw.get("created_at")) or EPOCH,
        summary="",
        url=f"{GITHUB_WEB}/{repo}",
        actor_avatar=actor.get("avatar_url", ""),
        action=payload.get("action") or "",
    )
    _HANDLERS.get(event.type, _other)(event, payload)
    return event


# ----- PR search (GraphQL) ----------------------------------------------


def _graphql_login(actor: dict | None) -> tuple[str, str]:
    """(login, avatar) of a GraphQL actor. Bots come without the "[bot]"
    suffix the REST API uses, so it is restored from __typename."""
    if not actor:
        return "ghost", ""
    login = actor.get("login") or "ghost"
    if actor.get("__typename") == "Bot" and not login.endswith("[bot]"):
        login = f"{login}[bot]"
    return login, actor.get("avatarUrl") or ""


def _search_event(
    pull: dict,
    type_: str,
    kind: EventKind,
    action: str,
    actor: dict | None,
    at: datetime,
    summary: str,
    url: str,
    details=None,
) -> GithubEvent:
    repo = (pull.get("repository") or {}).get("nameWithOwner", "")
    number = int(pull.get("number") or 0)
    login, avatar = _graphql_login(actor)
    state = _item_state(pull.get("state"), draft=bool(pull.get("isDraft")))
    if details is None:
        details = PullDetails(head_ref=pull.get("headRefName") or "", base_ref=pull.get("baseRefName") or "")
    return GithubEvent(
        id=f"{EventSource.SEARCH}:{repo}#{number}:{action}:{login}:{at.isoformat()}",
        kind=kind,
        type=type_,
        repo=repo,
        actor=login,
        created_at=at,
        summary=summary,
        url=url,
        actor_avatar=avatar,
        action=action,
        ref=pull.get("headRefName") or "",
        number=number,
        title=pull.get("title") or "",
        state=state,
        is_pull=True,
        source=EventSource.SEARCH,
        details=details,
        subject=_subject(SubjectType.PULL, repo, number),
    )


def events_from_search_pull(pull: dict, since: datetime) -> list[GithubEvent]:
    """Activity of one searched PR (GraphQL node) that happened since `since`.

    Closing without merge is left to the events API: the search node does not
    say who closed the PR."""
    number = int(pull.get("number") or 0)
    url = pull.get("url") or ""
    events = []
    created, merged = parse_timestamp(pull.get("createdAt")), parse_timestamp(pull.get("mergedAt"))
    if created and created >= since:
        events.append(
            _search_event(
                pull,
                "PullRequestEvent",
                EventKind.PR,
                "opened",
                pull.get("author"),
                created,
                f"{_PR_VERBS['opened']} #{number}",
                url,
            )
        )
    if merged and merged >= since:
        events.append(
            _search_event(
                pull,
                "PullRequestEvent",
                EventKind.PR,
                "merged",
                pull.get("mergedBy"),
                merged,
                f"{_PR_VERBS['merged']} #{number}",
                url,
            )
        )
    for review in (pull.get("reviews") or {}).get("nodes") or []:
        state = _review_state(review.get("state"))
        submitted = parse_timestamp(review.get("submittedAt"))
        if state is None or submitted is None or submitted < since:
            continue  # pending (not submitted) or unknown reviews are skipped
        verb = _REVIEW_VERBS.get(state, "reviewed PR")
        event = _search_event(
            pull,
            "PullRequestReviewEvent",
            EventKind.REVIEW,
            "created",
            review.get("author"),
            submitted,
            f"{verb} #{number}",
            review.get("url") or url,
            ReviewDetails(state=state),
        )
        event.body = _excerpt(review.get("body"))
        event.subject = _subject(SubjectType.REVIEW, event.repo, number, review.get("databaseId"))
        events.append(event)
    return events


# ----- full context of one subject (REST, html media type) ----------------


def context_from_api(subject: EventSubject, data: dict) -> EventContext:
    """The REST object behind a subject, read with the html media type, as
    an EventContext. Its body_html is sanitized here."""
    user = data.get("user") or data.get("author") or {}
    when = data.get("submitted_at") or data.get("published_at") or data.get("created_at")
    if subject.type == SubjectType.PULL:
        state = _pull_state(data)
    elif subject.type == SubjectType.ISSUE:
        state = _item_state(data.get("state"))
    else:
        state = None
    is_review = subject.type == SubjectType.REVIEW
    return EventContext(
        subject=subject,
        url=_html_url(data),
        author=user.get("login") or "",
        author_avatar=user.get("avatar_url") or "",
        created_at=parse_timestamp(when),
        title=data.get("title") or data.get("name") or "",
        state=state,
        review_state=_review_state(data.get("state")) if is_review else None,
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
        url=_html_url(data),
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
        state = _review_state(review.get("state"))
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
