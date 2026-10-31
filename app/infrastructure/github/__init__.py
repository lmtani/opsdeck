"""GithubProvider over GitHub's REST events API, the search API and GraphQL.

provider.py implements the port by delegating to one reader per view
(activity.py, contributions.py, context.py, repos.py), all over the shared
client.py (token, conditional GETs, pagination, rate limit). token.py
resolves and checks the token, search.py builds search queries, and
parsing/ turns the payloads into domain objects (pure functions);
html_sanitizer.py cleans the body_html GitHub renders before it reaches the page.
"""

from app.infrastructure.github.provider import GithubApiProvider
from app.infrastructure.github.token import resolve_github_token

__all__ = ["GithubApiProvider", "resolve_github_token"]
