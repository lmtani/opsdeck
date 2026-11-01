"""Contributions of the contributors view from GraphQL nodes: default-branch
commits and searched PRs/issues. Only github.com links are kept. Pure functions."""

from datetime import datetime

from opsdeck.domain.common import parse_timestamp
from opsdeck.domain.github import Contribution, ContributionKind
from opsdeck.infrastructure.github.parsing.common import github_url, graphql_login, review_state


def commit_contributions(repo: str, nodes: list[dict]) -> tuple[list[Contribution], int]:
    """Contributions of a default branch's history nodes, and how many
    commits were left out because their author email is not linked to a
    GitHub account. Merge commits are not counted: they hold no work of
    their own."""
    contributions, unlinked = [], 0
    for node in nodes:
        if ((node.get("parents") or {}).get("totalCount") or 0) > 1:
            continue
        at = parse_timestamp(node.get("committedDate"))
        user = (node.get("author") or {}).get("user")
        if at is None:
            continue
        if not user or not user.get("login"):
            unlinked += 1
            continue
        contributions.append(
            Contribution(
                user["login"],
                ContributionKind.COMMIT,
                repo,
                at,
                user.get("avatarUrl") or "",
                title=node.get("messageHeadline") or "",
                url=github_url(node.get("url")),
            )
        )
    return contributions, unlinked


def search_contributions(node: dict, since: datetime) -> list[Contribution]:
    """Contributions of a searched PR or issue (GraphQL node) since `since`:
    its opening and, for a PR, the reviews by anyone but its author (the
    author's own replies to review threads also come as reviews)."""
    repo = (node.get("repository") or {}).get("nameWithOwner", "")
    if not repo:
        return []
    is_pull = node.get("__typename") == "PullRequest"
    author, avatar = graphql_login(node.get("author"))
    number, title, url = int(node.get("number") or 0), node.get("title") or "", github_url(node.get("url"))
    contributions = []
    created = parse_timestamp(node.get("createdAt"))
    if created and created >= since:
        kind = ContributionKind.PR if is_pull else ContributionKind.ISSUE
        contributions.append(Contribution(author, kind, repo, created, avatar, number, title, url))
    reviews = (node.get("reviews") or {}).get("nodes") or [] if is_pull else []
    for review in filter(None, reviews):
        at = parse_timestamp(review.get("submittedAt"))  # None while pending
        login, review_avatar = graphql_login(review.get("author"))
        if at is None or at < since or login == author:
            continue
        review_url = github_url(review.get("url")) or url
        state = review_state(review.get("state"))
        contributions.append(
            Contribution(
                login, ContributionKind.REVIEW, repo, at, review_avatar, number, title, review_url, state
            )
        )
    return contributions
