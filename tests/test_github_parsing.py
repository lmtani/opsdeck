"""Normalization of raw GitHub events (payload shapes as served in 2026)."""

from app.domain.common import parse_timestamp
from app.domain.github import (
    CodeCommentDetails,
    EventKind,
    EventSource,
    PullDetails,
    ReviewDetails,
    ReviewState,
)
from app.domain.github_rules import compare_url, is_bot
from app.infrastructure.github_event_parsing import events_from_search_pull, parse_event

ACTOR = {
    "id": 1,
    "login": "alice",
    "display_login": "alice",
    "avatar_url": "https://avatars.githubusercontent.com/u/1?",
}


def _raw(type_, payload, repo="acme/core"):
    return {
        "id": "100",
        "type": type_,
        "actor": ACTOR,
        "repo": {"id": 9, "name": repo},
        "payload": payload,
        "public": False,
        "created_at": "2026-10-22T17:59:52Z",
    }


def test_push_links_the_compared_range():
    event = parse_event(
        _raw(
            "PushEvent",
            {
                "repository_id": 9,
                "push_id": 1,
                "ref": "refs/heads/main",
                "head": "3cd190fd7c526366cd7bb0c41608e10504b11aa6",
                "before": "55f827639276481bf18857294e58232d54a00d6f",
            },
        )
    )
    assert event.kind == "push"
    assert event.ref == "main"
    assert event.summary == "pushed to main"
    assert event.url == "https://github.com/acme/core/compare/55f8276...3cd190f"
    assert event.actor == "alice" and event.actor_avatar.startswith("https://avatars")


def test_push_of_a_new_branch_links_the_head_commit():
    assert compare_url("acme/core", "0" * 40, "abc") == "https://github.com/acme/core/commit/abc"


def test_trimmed_pull_request_event_has_number_but_no_title():
    event = parse_event(
        _raw(
            "PullRequestEvent",
            {
                "action": "merged",
                "number": 36,
                "pull_request": {
                    "number": 36,
                    "head": {"ref": "fix/front", "sha": "9928ac8"},
                    "base": {"ref": "main", "sha": "691bf11"},
                },
            },
        )
    )
    assert event.kind == "pr"
    assert event.summary == "merged PR #36"
    assert event.state == "merged"
    assert event.title == ""
    assert event.url == "https://github.com/acme/core/pull/36"
    assert event.is_pull is True
    assert event.details == PullDetails(head_ref="fix/front", base_ref="main")


def test_legacy_closed_and_merged_pull_request_counts_as_merge():
    event = parse_event(
        _raw(
            "PullRequestEvent",
            {
                "action": "closed",
                "number": 7,
                "pull_request": {
                    "number": 7,
                    "merged": True,
                    "state": "closed",
                    "title": "Fix",
                    "html_url": "https://github.com/acme/core/pull/7",
                },
            },
        )
    )
    assert event.action == "merged"
    assert event.state == "merged" and event.title == "Fix"


def test_review_state_drives_the_verb():
    event = parse_event(
        _raw(
            "PullRequestReviewEvent",
            {
                "action": "created",
                "review": {
                    "state": "approved",
                    "body": "LGTM",
                    "html_url": "https://github.com/acme/core/pull/12#pullrequestreview-1",
                },
                "pull_request": {"number": 12, "head": {"ref": "feat/x"}},
            },
        )
    )
    assert event.kind == "review"
    assert event.summary == "approved PR #12"
    assert event.body == "LGTM"
    assert event.url.endswith("#pullrequestreview-1")
    assert event.ref == "feat/x"


def test_review_comment_keeps_path_and_link():
    event = parse_event(
        _raw(
            "PullRequestReviewCommentEvent",
            {
                "action": "created",
                "comment": {
                    "path": "backend/x.py",
                    "body": "Poxa…",
                    "line": 21,
                    "_links": {"html": {"href": "https://github.com/acme/core/pull/13#discussion_r1"}},
                },
                "pull_request": {"number": 13},
            },
        )
    )
    assert event.kind == "comment"
    assert event.summary == "left a review comment on PR #13"
    assert event.url.endswith("#discussion_r1")
    assert event.is_pull is True
    assert event.details == CodeCommentDetails(path="backend/x.py", line=21)


def test_issue_comment_on_a_pull_request():
    event = parse_event(
        _raw(
            "IssueCommentEvent",
            {
                "action": "created",
                "issue": {
                    "number": 1102,
                    "title": "feat: coverage",
                    "state": "closed",
                    "html_url": "https://github.com/acme/core/pull/1102",
                    "pull_request": {"url": "x"},
                },
                "comment": {
                    "body": "ok",
                    "html_url": "https://github.com/acme/core/pull/1102#issuecomment-1",
                },
            },
        )
    )
    assert event.summary == "commented on PR #1102"
    assert event.title == "feat: coverage"
    assert event.state is None  # resolved later via GraphQL (closed may mean merged)
    assert event.is_pull is True and event.details is None


def test_branch_creation_and_deletion():
    created = parse_event(_raw("CreateEvent", {"ref": "fix/x", "ref_type": "branch"}))
    deleted = parse_event(_raw("DeleteEvent", {"ref": "v1.0", "ref_type": "tag"}))
    assert created.kind == deleted.kind == "branch"
    assert created.summary == "created branch fix/x"
    assert created.url == "https://github.com/acme/core/tree/fix/x"
    assert deleted.summary == "deleted tag v1.0"


def test_unknown_types_fall_back_to_other():
    event = parse_event(_raw("SponsorshipEvent", {}))
    assert event.kind == "other"
    assert event.summary == "Sponsorship"
    assert event.url == "https://github.com/acme/core"


def test_bots_are_recognized_by_login_suffix():
    assert is_bot("dependabot[bot]") and not is_bot("alice")


def test_bot_actors_keep_the_bot_suffix_from_login():
    raw = _raw("PushEvent", {"ref": "refs/heads/main", "before": "a", "head": "b"})
    raw["actor"] = {"login": "dependabot[bot]", "display_login": "dependabot"}
    event = parse_event(raw)
    assert event.actor == "dependabot[bot]"
    assert is_bot(event.actor)


# ----- PR search nodes (GraphQL) ---------------------------------------------


SEARCH_PULL = {
    "number": 112,
    "title": "feat(bwa-mem3-picard): imagem",
    "url": "https://github.com/acme/dockerfiles/pull/112",
    "state": "MERGED",
    "isDraft": False,
    "createdAt": "2026-10-20T14:57:38Z",
    "mergedAt": "2026-10-23T12:12:17Z",
    "closedAt": "2026-10-23T12:12:17Z",
    "headRefName": "feat/bwa-mem3",
    "baseRefName": "main",
    "repository": {"nameWithOwner": "acme/dockerfiles"},
    "author": {"__typename": "User", "login": "alice", "avatarUrl": "https://a/1"},
    "mergedBy": {"__typename": "User", "login": "renan", "avatarUrl": "https://a/2"},
    "reviews": {
        "nodes": [
            {
                "state": "APPROVED",
                "submittedAt": "2026-10-23T12:12:06Z",
                "body": "",
                "url": "https://github.com/acme/dockerfiles/pull/112#pullrequestreview-1",
                "author": {"__typename": "User", "login": "renan", "avatarUrl": "https://a/2"},
            },
            {"state": "PENDING", "submittedAt": None, "author": {"login": "x"}},
            {"state": "COMMENTED", "submittedAt": "2026-10-19T10:00:00Z", "author": {"login": "y"}},
        ]
    },
}


def test_search_pull_becomes_merge_and_review_events_inside_the_window():
    events = events_from_search_pull(SEARCH_PULL, since=parse_timestamp("2026-10-23T00:00:00Z"))
    merged, review = events  # opened is older than the window; pending/old reviews skipped
    assert (merged.type, merged.action, merged.actor) == ("PullRequestEvent", "merged", "renan")
    assert merged.summary == "merged PR #112"
    assert merged.state == "merged" and merged.title.startswith("feat(bwa-mem3")
    assert merged.source is EventSource.SEARCH and merged.is_pull is True
    assert merged.details == PullDetails(head_ref="feat/bwa-mem3", base_ref="main")
    assert (review.kind, review.details) == (EventKind.REVIEW, ReviewDetails(ReviewState.APPROVED))
    assert review.summary == "approved PR #112"
    assert review.url.endswith("#pullrequestreview-1")


def test_search_pull_opened_and_bot_authors():
    pull = dict(
        SEARCH_PULL,
        state="OPEN",
        isDraft=True,
        mergedAt=None,
        closedAt=None,
        reviews={"nodes": []},
        author={"__typename": "Bot", "login": "dependabot", "avatarUrl": ""},
    )
    (opened,) = events_from_search_pull(pull, since=parse_timestamp("2026-10-20T00:00:00Z"))
    assert opened.action == "opened" and opened.state == "draft"
    assert opened.actor == "dependabot[bot]" and is_bot(opened.actor)
