"""The repositories of an org (or a user account), most recently pushed first:
the feed modal's repo picker and the repos the contributors view reads."""

from opsdeck.domain.common import EPOCH, parse_timestamp
from opsdeck.domain.errors import GithubNotFoundError
from opsdeck.domain.github import GithubRepo
from opsdeck.infrastructure.github.client import GithubClient

MAX_PAGES = 10


def org_repos(client: GithubClient, org: str) -> list[GithubRepo]:
    try:
        raw = client.paged(f"/orgs/{org}/repos", MAX_PAGES, type="all", sort="pushed")
    except GithubNotFoundError:  # a user account, not an org
        raw = client.paged(f"/users/{org}/repos", MAX_PAGES, type="owner", sort="pushed")
    repos = [
        GithubRepo(
            name=item.get("name", ""),
            full_name=item.get("full_name", ""),
            private=bool(item.get("private")),
            archived=bool(item.get("archived")),
            description=item.get("description") or "",
            pushed_at=parse_timestamp(item.get("pushed_at")),
            url=item.get("html_url", ""),
        )
        for item in raw
    ]
    return sorted(repos, key=lambda repo: repo.pushed_at or EPOCH, reverse=True)
