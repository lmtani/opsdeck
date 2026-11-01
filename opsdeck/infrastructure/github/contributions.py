"""What the contributors and network views read.

GitHub's own contribution graph (contributionsCollection) hides other
people's private-repo work, so contributions are read from the repositories:
each default branch's history (a few repos per GraphQL query) and a search
for PRs updated and issues created in the window, all in one thread pool. A
snapshot is cached for 10 minutes, so the day list and the network view
answer from the grid's read.
"""

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from opsdeck.domain.github import Contribution, ContributionsSnapshot, FeedSpec
from opsdeck.infrastructure.github.client import GithubClient
from opsdeck.infrastructure.github.parsing import commit_contributions, search_contributions
from opsdeck.infrastructure.github.repos import org_repos
from opsdeck.infrastructure.github.search import search_queries, search_stamp

HISTORY_REPOS_PER_QUERY = 10
HISTORY_MAX_PAGES = 10  # per repo, 100 commits each
CONTRIBUTION_WORKERS = 6
HISTORY_PART = """
r{index}: repository(owner: {owner}, name: {name}) {{
  defaultBranchRef {{ target {{ ... on Commit {{
    history(first: 100, since: {since}{after}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        committedDate messageHeadline url parents {{ totalCount }}
        author {{ user {{ login avatarUrl }} }}
      }}
    }}
  }} }} }}
}}"""
# GitHub serves at most 1000 results per search, page after page; the PR
# search is cut into slices of `updated` time, read in parallel
CONTRIBUTION_SEARCH_MAX_PAGES = 10
CONTRIBUTION_SEARCH_SLICE = timedelta(days=7)
CONTRIBUTION_SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      ... on PullRequest {
        id
        number title url createdAt
        repository { nameWithOwner }
        author { __typename login avatarUrl }
        reviews(first: 50) { nodes { submittedAt state url author { __typename login avatarUrl } } }
      }
      ... on Issue {
        id
        number title url createdAt
        repository { nameWithOwner }
        author { __typename login avatarUrl }
      }
    }
  }
}
"""


def contribution_search_queries(org: str, repos: list[str], since: datetime, now: datetime) -> list[str]:
    """The searches of the contributors view: PRs updated since `since` (a
    review updates its PR), in CONTRIBUTION_SEARCH_SLICE slices of update
    time so they can run in parallel and each stays under the 1000-result
    cap, plus issues created since `since`."""
    queries = []
    start = since
    while True:
        end = start + CONTRIBUTION_SEARCH_SLICE
        span = f">={search_stamp(start)}" if end >= now else f"{search_stamp(start)}..{search_stamp(end)}"
        queries += search_queries(org, repos, "", base=f"is:pr updated:{span}")
        if end >= now:
            break
        start = end
    return queries + search_queries(org, repos, "", base=f"is:issue created:>={search_stamp(since)}")


class ContributionsReader:
    def __init__(
        self,
        client: GithubClient,
        clock: Callable[[], float],
        cache_seconds: float,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._client = client
        self._clock = clock
        self._now = now
        self._cache_seconds = cache_seconds
        self._cache: dict[tuple, tuple[float, ContributionsSnapshot]] = {}
        self._lock = threading.Lock()

    def contributions(self, spec: FeedSpec, since: datetime) -> ContributionsSnapshot:
        key = (spec.org, spec.repos, since.astimezone(UTC).isoformat())
        cached = self._cache.get(key)
        if cached and cached[0] > self._clock():
            return cached[1]
        viewer = self._client.viewer()
        if spec.repos:
            repos = list(spec.repos)
        else:  # only repos pushed in the window can have new commits
            repos = [
                repo.name
                for repo in org_repos(self._client, spec.org)
                if repo.pushed_at and repo.pushed_at >= since
            ]
        chunks = [
            repos[i : i + HISTORY_REPOS_PER_QUERY] for i in range(0, len(repos), HISTORY_REPOS_PER_QUERY)
        ]
        queries = contribution_search_queries(spec.org, list(spec.repos), since, self._now())
        with ThreadPoolExecutor(max_workers=CONTRIBUTION_WORKERS) as pool:
            histories = [pool.submit(self._history_contributions, spec.org, chunk, since) for chunk in chunks]
            searches = [pool.submit(self._search_nodes, query) for query in queries]
            history_results = [future.result() for future in histories]
            search_results = [future.result() for future in searches]

        contributions: list[Contribution] = []
        missing: list[str] = []
        unlinked = 0
        for found, chunk_missing, chunk_unlinked in history_results:
            contributions += found
            missing += chunk_missing
            unlinked += chunk_unlinked
        nodes: dict[str, dict] = {}  # by node id: a PR updated on a slice boundary is found twice
        warnings: list[str] = []
        for query, (found_nodes, total) in zip(queries, search_results, strict=True):
            nodes.update((node.get("id") or str(id(node)), node) for node in found_nodes)
            if total > len(found_nodes):
                warnings.append(
                    f"Search returned {len(found_nodes)} of {total} items for `{query}`: some PRs, reviews"
                    " or issues are missing. Pick a shorter period or fewer repositories."
                )
        for node in nodes.values():
            contributions += search_contributions(node, since)
        if missing and spec.repos:
            warnings.insert(
                0,
                f"Could not read the history of {', '.join(missing)}:"
                " missing, empty or not visible to the token.",
            )
        if unlinked:
            warnings.append(
                f"{unlinked} commit{'s' if unlinked > 1 else ''} left out:"
                " the author email is not linked to a GitHub account."
            )
        snapshot = ContributionsSnapshot(viewer, contributions, warnings, self._client.quota())
        with self._lock:
            self._cache[key] = (self._clock() + self._cache_seconds, snapshot)
        return snapshot

    def _history_contributions(
        self, org: str, repos: list[str], since: datetime
    ) -> tuple[list[Contribution], list[str], int]:
        """Commits on the default branch of a few repos, one query per page
        round; returns them, the repos that could not be read and how many
        commits had no linked account."""
        contributions: list[Contribution] = []
        missing: list[str] = []
        unlinked = 0
        cursors: dict[str, str | None] = dict.fromkeys(repos)
        pages = 0
        while cursors and pages < HISTORY_MAX_PAGES:
            pages += 1
            names = list(cursors)
            parts = [
                HISTORY_PART.format(
                    index=index,
                    owner=json.dumps(org),
                    name=json.dumps(name),
                    since=json.dumps(search_stamp(since)),
                    after=f", after: {json.dumps(cursors[name])}" if cursors[name] else "",
                )
                for index, name in enumerate(names)
            ]
            data = self._client.graphql("query { " + " ".join(parts) + " }", {})
            for index, name in enumerate(names):
                history = _history(data.get(f"r{index}"))
                if history is None:
                    missing.append(f"{org}/{name}")
                    cursors.pop(name)
                    continue
                found, no_account = commit_contributions(f"{org}/{name}", history.get("nodes") or [])
                contributions += found
                unlinked += no_account
                page = history.get("pageInfo") or {}
                if page.get("hasNextPage"):
                    cursors[name] = page.get("endCursor")
                else:
                    cursors.pop(name)
        return contributions, missing, unlinked

    def _search_nodes(self, query: str) -> tuple[list[dict], int]:
        """The PRs or issues one search finds (up to GitHub's 1000), and how
        many it says there are."""
        nodes: list[dict] = []
        after = None
        total = 0
        for _ in range(CONTRIBUTION_SEARCH_MAX_PAGES):
            search = (
                self._client.graphql(CONTRIBUTION_SEARCH_QUERY, {"q": query, "after": after}).get("search")
                or {}
            )
            total = int(search.get("issueCount") or 0)
            nodes.extend(node for node in search.get("nodes") or [] if node)
            page = search.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
        return nodes, total


def _history(repository: Any) -> dict | None:
    """The default branch's history of a GraphQL repository node; None when
    the repo is missing, invisible or empty."""
    branch = (repository or {}).get("defaultBranchRef") if isinstance(repository, dict) else None
    target = (branch or {}).get("target") or {}
    return target.get("history")
