"""Helpers shared by the parsers: URLs, states and GraphQL logins read the
same way from every payload."""

from opsdeck.domain.github import ItemState, ReviewState
from opsdeck.domain.github_rules import GITHUB_WEB


def html_url(obj: dict | None) -> str:
    obj = obj or {}
    return obj.get("html_url") or ((obj.get("_links") or {}).get("html") or {}).get("href", "")


def item_state(value: str | None, draft: bool = False, merged: bool = False) -> ItemState | None:
    if merged:
        return ItemState.MERGED
    state = (value or "").lower()
    if state == "open" and draft:
        return ItemState.DRAFT
    try:
        return ItemState(state)
    except ValueError:
        return None


def pull_state(pull: dict) -> ItemState | None:
    merged = bool(pull.get("merged") or pull.get("merged_at"))
    return item_state(pull.get("state"), draft=bool(pull.get("draft")), merged=merged)


def review_state(value: str | None) -> ReviewState | None:
    try:
        return ReviewState((value or "").lower())
    except ValueError:
        return None


def graphql_login(actor: dict | None) -> tuple[str, str]:
    """(login, avatar) of a GraphQL actor. Bots come without the "[bot]"
    suffix the REST API uses, so it is restored from __typename."""
    if not actor:
        return "ghost", ""
    login = actor.get("login") or "ghost"
    if actor.get("__typename") == "Bot" and not login.endswith("[bot]"):
        login = f"{login}[bot]"
    return login, actor.get("avatarUrl") or ""


def github_url(url) -> str:
    """Only github.com links reach the page."""
    return url if isinstance(url, str) and url.startswith(f"{GITHUB_WEB}/") else ""
