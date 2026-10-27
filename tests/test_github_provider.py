"""GithubApiProvider over httpx.MockTransport — no real network."""

import json

import httpx
import pytest

from app.domain.common import parse_timestamp
from app.domain.errors import GithubError
from app.domain.github import FeedSpec, Quota, TokenKind
from app.infrastructure.github_api_provider import GithubApiProvider, search_queries

USER = {"login": "alice"}


def _event(index, type_="WatchEvent"):
    return {
        "id": str(index),
        "type": type_,
        "actor": {"login": "bob"},
        "repo": {"name": "acme/core"},
        "payload": {},
        "created_at": "2026-10-22T10:00:00Z",
    }


def _provider(handler, token="t0ken", **kwargs):
    return GithubApiProvider(token_source=lambda: token, transport=httpx.MockTransport(handler), **kwargs)


def test_conditional_requests_reuse_the_body_on_304():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json=[_event(1)], headers={"etag": '"v1"'})

    provider = _provider(handler)
    first = provider.org_events("acme").events
    second = provider.org_events("acme").events
    assert [e.id for e in first] == [e.id for e in second] == ["1"]
    event_calls = [c for c in calls if c.url.path != "/user"]
    assert event_calls[0].url.path == "/users/alice/events/orgs/acme"
    assert "if-none-match" not in event_calls[0].headers
    assert event_calls[1].headers["if-none-match"] == '"v1"'
    assert calls[0].headers["authorization"] == "Bearer t0ken"


def test_org_events_follow_the_link_header_not_the_page_size():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        page = int(request.url.params["page"])
        # GitHub sometimes serves 99 items on a page that still has a successor
        headers = (
            {"link": f'<{request.url.copy_set_param("page", page + 1)}>; rel="next"'} if page < 2 else {}
        )
        return httpx.Response(200, json=[_event(page * 1000 + i) for i in range(99)], headers=headers)

    batch = _provider(handler).org_events("acme")
    assert len(batch.events) == 198
    assert batch.truncated is False  # page 2 had no successor


def test_falls_back_to_public_org_events_when_not_a_member():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        if request.url.path.startswith("/users/alice/events/orgs/"):
            return httpx.Response(404, json={"message": "Not Found"})
        assert request.url.path == "/orgs/nf-core/events"
        return httpx.Response(200, json=[_event(7)])

    assert [e.id for e in _provider(handler).org_events("nf-core").events] == ["7"]


def test_rate_limit_exhaustion_is_reported_clearly():
    def handler(request):
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": "9999999999",
            },
        )

    provider = _provider(handler)
    with pytest.raises(GithubError, match="rate limit exhausted"):
        provider.repo_events("acme", "core")
    assert provider.quota() == Quota(remaining=0, limit=5000)


def test_missing_token_fails_without_calling_github():
    def handler(request):
        raise AssertionError("no request expected")

    provider = _provider(handler, token="")
    assert provider.is_available() is False
    with pytest.raises(GithubError, match="No GitHub token"):
        provider.viewer()


def test_pull_requests_are_batched_in_graphql_and_cached():
    queries = []

    def handler(request):
        assert request.url.path == "/graphql"
        queries.append(request.content.decode())
        return httpx.Response(
            200,
            json={
                "data": {
                    "r0": {
                        "p5": {
                            "title": "Fix",
                            "state": "MERGED",
                            "isDraft": False,
                            "url": "https://github.com/acme/core/pull/5",
                        },
                        "p6": {"title": "WIP", "state": "OPEN", "isDraft": True, "url": ""},
                        "p7": None,
                    },
                }
            },
        )

    provider = _provider(handler)
    refs = [("acme/core", 5), ("acme/core", 6), ("acme/core", 7)]
    info = provider.pull_requests(refs)
    assert info[("acme/core", 5)].state == "merged"
    assert info[("acme/core", 6)].state == "draft"
    assert info[("acme/core", 6)].url == "https://github.com/acme/core/pull/6"
    assert ("acme/core", 7) not in info
    assert len(queries) == 1 and '\\"acme\\"' in queries[0]

    provider.pull_requests(refs[:2])  # cached: no new query
    assert len(queries) == 1


def test_org_repos_are_sorted_by_last_push():
    def handler(request):
        assert request.url.params["type"] == "all"
        return httpx.Response(
            200,
            json=[
                {"name": "old", "full_name": "acme/old", "pushed_at": "2025-01-01T00:00:00Z"},
                {
                    "name": "new",
                    "full_name": "acme/new",
                    "pushed_at": "2026-10-01T00:00:00Z",
                    "private": True,
                },
            ],
        )

    repos = _provider(handler).org_repos("acme")
    assert [r.name for r in repos] == ["new", "old"]
    assert repos[0].private is True


def test_a_read_cut_by_the_page_limit_is_flagged_truncated():
    def handler(request):
        page = int(request.url.params["page"])
        link = f'<{request.url.copy_set_param("page", page + 1)}>; rel="next"'
        return httpx.Response(200, json=[_event(page)], headers={"link": link})

    batch = _provider(handler, repo_pages=2).repo_events("acme", "core")
    assert [e.id for e in batch.events] == ["1", "2"]
    assert batch.truncated is True


def test_a_read_that_hits_the_300_event_cap_is_truncated_without_next_link():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        page = int(request.url.params["page"])
        # GitHub's last events page has no "next" link, history goes further back
        link = {"link": f'<{request.url.copy_set_param("page", page + 1)}>; rel="next"'} if page < 3 else {}
        return httpx.Response(200, json=[_event(page * 1000 + i) for i in range(99)], headers=link)

    batch = _provider(handler).org_events("acme")
    assert len(batch.events) == 297
    assert batch.truncated is True


def _search_node(number, merged_at):
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/acme/core/pull/{number}",
        "state": "MERGED",
        "isDraft": False,
        "createdAt": "2026-10-01T00:00:00Z",
        "mergedAt": merged_at,
        "closedAt": merged_at,
        "headRefName": "x",
        "baseRefName": "main",
        "repository": {"nameWithOwner": "acme/core"},
        "author": {"login": "alice"},
        "mergedBy": {"login": "bob"},
        "reviews": {"nodes": []},
    }


def test_recent_pull_request_events_page_through_search_and_cache_it():
    calls = []
    now = [1000.0]

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["variables"])
        if body["variables"]["after"] is None:
            page = {"hasNextPage": True, "endCursor": "c1"}
            nodes = [_search_node(1, "2026-10-23T12:00:00Z")]
        else:
            page = {"hasNextPage": False, "endCursor": None}
            nodes = [_search_node(2, "2026-10-23T11:00:00Z"), {}]  # {} = non-PR hit
        return httpx.Response(200, json={"data": {"search": {"pageInfo": page, "nodes": nodes}}})

    provider = _provider(handler, clock=lambda: now[0])
    events = provider.recent_pull_request_events("acme", [], parse_timestamp("2026-10-23T00:00:30Z"))
    assert [(e.number, e.action) for e in events] == [(1, "merged"), (2, "merged")]
    assert calls[0]["q"] == "org:acme is:pr updated:>=2026-10-23T00:00:00Z"
    assert len(calls) == 2

    later = parse_timestamp("2026-10-23T00:00:45Z")
    provider.recent_pull_request_events("acme", [], later)  # same minute: cached
    assert len(calls) == 2
    now[0] += 61
    provider.recent_pull_request_events("acme", [], later)
    assert len(calls) == 4


def test_search_queries_split_repo_qualifiers_under_the_length_limit():
    assert search_queries("acme", [], "T") == ["org:acme is:pr updated:>=T"]
    repos = [f"repository-number-{i:02d}" for i in range(20)]
    queries = search_queries("acme", repos, "2026-10-23T00:00:00Z")
    assert len(queries) > 1
    assert all(len(q) <= 256 for q in queries)
    joined = " ".join(queries)
    assert all(f"repo:acme/{repo}" in joined for repo in repos)


# ----- activity: the whole read for a feed spec -------------------------------

SINCE = parse_timestamp("2026-10-23T00:00:00Z")


def _graphql_empty():
    return httpx.Response(200, json={"data": {"search": {"pageInfo": {"hasNextPage": False}, "nodes": []}}})


def test_activity_reads_repos_in_parallel_and_warns_about_the_missing_one():
    threads = set()

    def handler(request):
        import threading

        threads.add(threading.get_ident())
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        if request.url.path == "/graphql":
            return _graphql_empty()
        if request.url.path == "/repos/acme/gone/events":
            return httpx.Response(404, json={"message": "Not Found"})
        repo = request.url.path.split("/")[3]
        return httpx.Response(
            200,
            json=[_event(f"{repo}-1")],
            headers={"x-ratelimit-remaining": "4990", "x-ratelimit-limit": "5000"},
        )

    snapshot = _provider(handler).activity(FeedSpec("acme", repos=("core", "api", "gone")), SINCE)
    assert snapshot.viewer == "alice"
    assert [[e.id for e in b.events] for b in snapshot.batches] == [["core-1"], ["api-1"], []]
    assert snapshot.warnings == ["acme/gone does not exist or the token cannot see it."]
    assert snapshot.quota == Quota(remaining=4990, limit=5000)
    assert len(threads) > 1  # repos were fetched concurrently


def test_activity_fails_only_when_every_repo_fails():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        return httpx.Response(500, json={"message": "boom"})

    with pytest.raises(GithubError, match="500"):
        _provider(handler).activity(FeedSpec("acme", repos=("core", "api")), SINCE)


def test_activity_degrades_a_failed_realtime_search_to_a_warning():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"errors": [{"message": "search is down"}]})
        return httpx.Response(200, json=[_event(1)])

    snapshot = _provider(handler).activity(FeedSpec("acme"), SINCE)
    assert [e.id for b in snapshot.batches for e in b.events] == ["1"]
    assert snapshot.realtime == []
    assert snapshot.warnings == ["Real-time PR search unavailable: GitHub GraphQL failed: search is down"]


# ----- token diagnostics -------------------------------------------------------


def _user_handler(scopes=None, status=200, message=""):
    def handler(request):
        if request.url.path == "/user":
            if status != 200:
                return httpx.Response(status, json={"message": message})
            headers = {} if scopes is None else {"x-oauth-scopes": scopes}
            return httpx.Response(200, json=USER, headers=headers)
        raise AssertionError(f"unexpected {request.url.path}")

    return handler


def test_token_status_without_a_token():
    status = _provider(_user_handler(), token="").token_status()
    assert status.kind is TokenKind.NONE and not status.usable
    assert "No GitHub token" in status.problems[0]


def test_classic_token_with_the_right_scopes_is_usable():
    status = _provider(_user_handler("repo, read:org, gist"), token="ghp_x").token_status()
    assert (status.kind, status.login, status.usable) == (TokenKind.CLASSIC, "alice", True)
    assert status.scopes == ("repo", "read:org", "gist") and status.problems == []


def test_missing_scopes_are_named():
    status = _provider(_user_handler("gist"), token="ghp_x").token_status()
    assert not status.usable
    assert "`repo` and `read:org` scopes" in status.problems[0]
    oauth = _provider(_user_handler("repo, admin:org"), token="gho_x").token_status()
    assert oauth.kind is TokenKind.OAUTH and oauth.usable  # admin:org covers read:org


def test_fine_grained_token_gets_setup_notes():
    status = _provider(_user_handler(None), token="github_pat_x").token_status()
    assert status.kind is TokenKind.FINE_GRAINED and status.usable and status.scopes is None
    assert "Resource owner to the organization" in status.notes[0]


def test_rejected_token_explains_what_to_do():
    status = _provider(_user_handler(status=401, message="Bad credentials"), token="ghp_x").token_status()
    assert not status.usable and "invalid, expired or revoked" in status.problems[0]


def test_saml_and_fine_grained_refusals_get_specific_hints():
    saml = (
        "Resource protected by organization SAML enforcement."
        " You must grant your Personal Access token access to this organization."
    )
    status = _provider(_user_handler(status=403, message=saml), token="ghp_x").token_status()
    assert "Configure SSO" in status.problems[0]
    policy = (
        "The 'acme' organization forbids access via a fine-grained personal access tokens"
        " if the token's lifetime is greater than 7 days."
    )
    status = _provider(_user_handler(status=403, message=policy), token="github_pat_x").token_status()
    assert "restricts fine-grained tokens" in status.problems[0]


def test_org_feed_that_falls_back_to_public_events_says_so():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json=USER)
        if request.url.path == "/graphql":
            return httpx.Response(
                200, json={"data": {"search": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}
            )
        if request.url.path.startswith("/users/alice/events/orgs/"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[_event(1)])

    snapshot = _provider(handler, token="github_pat_x").activity(FeedSpec("acme"), SINCE)
    assert [e.id for b in snapshot.batches for e in b.events] == ["1"]
    assert snapshot.warnings[0].startswith("Only public events of acme are shown")
    assert "Resource owner to the organization" in snapshot.warnings[0]
