"""The full context of one event's subject (the side panel): the item read
with the html media type and, around it, a review comment's thread, a
review's line comments or a pull request's reviews.

The REST path is built from the subject's type, repo and ids, never from a
URL the client sent.
"""

from dataclasses import replace

from app.domain.errors import GithubNotFoundError
from app.domain.github import EventContext, EventSubject, SubjectType, ThreadComment
from app.infrastructure.github.client import GithubClient
from app.infrastructure.github.parsing import (
    context_from_api,
    pull_number,
    pull_stats_from_api,
    review_thread,
    thread_comment_from_api,
)

_CONTEXT_PATHS = {
    SubjectType.PULL: "/repos/{repo}/pulls/{number}",
    SubjectType.ISSUE: "/repos/{repo}/issues/{number}",
    SubjectType.REVIEW: "/repos/{repo}/pulls/{number}/reviews/{item_id}",
    SubjectType.ISSUE_COMMENT: "/repos/{repo}/issues/comments/{item_id}",
    SubjectType.REVIEW_COMMENT: "/repos/{repo}/pulls/comments/{item_id}",
    SubjectType.RELEASE: "/repos/{repo}/releases/{item_id}",
    SubjectType.COMMIT_COMMENT: "/repos/{repo}/comments/{item_id}",
}
# pages of 100 read around one event: a thread's replies, a review's line
# comments, a pull request's reviews
CONTEXT_PAGES = 3


def event_context(client: GithubClient, subject: EventSubject) -> EventContext:
    path = _CONTEXT_PATHS[subject.type].format(
        repo=subject.repo, number=subject.number, item_id=subject.item_id
    )
    data = client.read_html(path)
    context = context_from_api(subject, data)
    base = f"/repos/{subject.repo}/pulls"
    if subject.type == SubjectType.PULL:
        reviews = client.read_html_pages(f"{base}/{subject.number}/reviews", CONTEXT_PAGES)
        return replace(context, pull=pull_stats_from_api(data, reviews))
    if subject.type == SubjectType.REVIEW:
        comments = client.read_html_pages(
            f"{base}/{subject.number}/reviews/{subject.item_id}/comments", CONTEXT_PAGES
        )
        return replace(context, thread=tuple(thread_comment_from_api(c) for c in comments))
    if subject.type == SubjectType.REVIEW_COMMENT:
        return replace(context, thread=_review_thread(client, subject.repo, data))
    return context


def _review_thread(client: GithubClient, repo: str, comment: dict) -> tuple[ThreadComment, ...]:
    """The thread a review comment belongs to: its root (fetched when the
    comment is a reply) and every reply posted since the root."""
    root = comment
    root_id = comment.get("in_reply_to_id")
    if isinstance(root_id, int):
        try:
            root = client.read_html(f"/repos/{repo}/pulls/comments/{root_id}")
        except GithubNotFoundError:
            root = comment  # the root was deleted: the reply stands alone
    number = pull_number(comment.get("pull_request_url"))
    if not number:
        return review_thread(root, [comment])
    replies = client.read_html_pages(
        f"/repos/{repo}/pulls/{number}/comments", CONTEXT_PAGES, since=root.get("created_at") or ""
    )
    return review_thread(root, [*replies, comment])
