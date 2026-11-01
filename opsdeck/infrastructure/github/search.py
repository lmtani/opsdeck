"""Search query strings shared by the timeline's real-time PR search and the
contributors view. Pure functions."""

from datetime import UTC, datetime

# GitHub rejects search queries longer than 256 characters; repo qualifiers
# are split across several searches below that.
SEARCH_QUERY_MAX = 240


def search_queries(org: str, repos: list[str], since: str, base: str = "") -> list[str]:
    """Search queries for PRs updated since `since` (or for `base`): one for
    the whole org, or repo qualifiers (OR-ed by GitHub) packed under
    SEARCH_QUERY_MAX."""
    base = base or f"is:pr updated:>={since}"
    if not repos:
        return [f"org:{org} {base}"]
    queries, current = [], base
    for repo in repos:
        qualifier = f" repo:{org}/{repo}"
        if current != base and len(current) + len(qualifier) > SEARCH_QUERY_MAX:
            queries.append(current)
            current = base
        current += qualifier
    queries.append(current)
    return queries


def search_stamp(at: datetime) -> str:
    """An instant as the search syntax and GraphQL `since` arguments take it."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
