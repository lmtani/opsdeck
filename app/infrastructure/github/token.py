"""The GitHub token: where it comes from (GITHUB_TOKEN / GH_TOKEN, else the
local `gh auth token`), what kind it is, and what it is missing.

Classic PAT scopes needed: `repo` (private repos) and `read:org`. The hints
here are also what the client appends to 401/403 answers.
"""

import shutil
import subprocess
from typing import TYPE_CHECKING

from app.domain.errors import GithubError
from app.domain.github import TokenKind, TokenStatus

if TYPE_CHECKING:  # the client imports the hints below
    from app.infrastructure.github.client import GithubClient

README_HINT = "See the GitHub tab section of the README."
CLASSIC_HINT = "A classic token with the `repo` and `read:org` scopes is the simplest setup."
FINE_GRAINED_HINT = (
    "A fine-grained token only works for one owner: set Resource owner to the organization,"
    " give it access to the repositories you follow and grant Organization permissions →"
    " Events (read) and Repository permissions → Metadata, Pull requests and, for the contributors"
    " view, Contents (read)."
    " The organization may have to approve it, or may not allow fine-grained tokens at all."
)
_ORG_SCOPES = {"read:org", "write:org", "admin:org"}
_TOKEN_PREFIXES = {
    "ghp_": TokenKind.CLASSIC,
    "github_pat_": TokenKind.FINE_GRAINED,
    "gho_": TokenKind.OAUTH,
}


def resolve_github_token(configured: str = "") -> str:
    """The configured token, else the token of the local `gh` login, else ""."""
    if configured:
        return configured
    gh = shutil.which("gh")
    if not gh:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments, gh resolved from PATH
            [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def token_kind(token: str) -> TokenKind:
    if not token:
        return TokenKind.NONE
    for prefix, kind in _TOKEN_PREFIXES.items():
        if token.startswith(prefix):
            return kind
    return TokenKind.OTHER


def token_status(client: "GithubClient") -> TokenStatus:
    """Who the token belongs to and what keeps it from seeing everything."""
    kind = token_kind(client.token(raise_if_missing=False))
    if kind is TokenKind.NONE:
        return TokenStatus(
            kind, problems=[f"No GitHub token: set GITHUB_TOKEN or run `gh auth login`. {README_HINT}"]
        )
    try:
        response = client.send("GET", "/user")
    except GithubError as error:
        return TokenStatus(kind, problems=[str(error)])
    login = (response.json() or {}).get("login", "")
    client.remember_viewer(login)
    # classic and OAuth tokens list their scopes; fine-grained ones do not
    header = response.headers.get("x-oauth-scopes")
    scopes = None if header is None else tuple(scope.strip() for scope in header.split(",") if scope.strip())
    status = TokenStatus(kind, login=login, scopes=scopes)
    if scopes is not None and (missing := _missing_scopes(scopes)):
        status.problems.append(
            f"The token is missing the {' and '.join(f'`{s}`' for s in missing)}"
            f" scope{'s' if len(missing) > 1 else ''}, so private repositories and"
            f" organization activity stay hidden. {CLASSIC_HINT}"
        )
    if kind is TokenKind.FINE_GRAINED:
        status.notes.append(FINE_GRAINED_HINT)
    return status


def _missing_scopes(scopes: tuple[str, ...]) -> list[str]:
    missing = []
    if "repo" not in scopes:
        missing.append("repo")
    if not _ORG_SCOPES & set(scopes):
        missing.append("read:org")
    return missing
