"""HTTP layer over in-memory fakes: status codes, error mapping and the
route precedence that depends on declaration order (/order, /unseen)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from opsdeck.application.backup import BackupService
from opsdeck.application.github import GithubActivityService, GithubFeedService
from opsdeck.application.links import CommandService, ImageService, LinkService
from opsdeck.application.logs import LogFilterService, LogQueryService, LogSourceService, LogWatchService
from opsdeck.application.settings import SettingsService
from opsdeck.application.sharing import SharingService
from opsdeck.application.updates import UpdateService
from opsdeck.domain.common import utc_now
from opsdeck.domain.errors import CommandError, GithubError, LogProviderError
from opsdeck.domain.github import (
    Contribution,
    ContributionKind,
    EventContext,
    EventSubject,
    Label,
    PullStats,
    Quota,
    Reviewer,
    ReviewState,
    SubjectType,
    ThreadComment,
)
from opsdeck.domain.logs import LogEntry, LogSourceKind, SeverityBand
from opsdeck.domain.updates import Release
from opsdeck.presentation.api import Services, create_app
from tests.fakes import (
    FakeCommandRunner,
    FakeGithubProvider,
    FakeImageStore,
    FakeLogProvider,
    FakeReleaseFeed,
    InMemoryFeedRepository,
    InMemoryFilterRepository,
    InMemoryLinkRepository,
    InMemorySettingsRepository,
    InMemorySourceRepository,
    make_code_comment,
    make_event,
    make_push,
)


@pytest.fixture
def world(tmp_path):
    fakes = SimpleNamespace(
        links=InMemoryLinkRepository(),
        filters=InMemoryFilterRepository(),
        sources=InMemorySourceRepository(),
        feeds=InMemoryFeedRepository(),
        settings=InMemorySettingsRepository(),
        runner=FakeCommandRunner(),
        logs=FakeLogProvider(),
        github=FakeGithubProvider(),
        images=FakeImageStore(),
        releases=FakeReleaseFeed(),
        now=datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
    )
    services = Services(
        links=LinkService(fakes.links),
        commands=CommandService(fakes.runner, fakes.links),
        sources=LogSourceService(fakes.sources, fakes.filters),
        filters=LogFilterService(fakes.filters, fakes.sources),
        logs=LogQueryService({LogSourceKind.GCP: fakes.logs}, fakes.sources, fakes.filters),
        watch=LogWatchService(
            {LogSourceKind.GCP: fakes.logs}, fakes.sources, fakes.filters, clock=lambda: fakes.now
        ),
        settings=SettingsService(fakes.settings),
        images=ImageService(fakes.images),
        github_feeds=GithubFeedService(fakes.feeds),
        github=GithubActivityService(fakes.github, fakes.feeds),
        sharing=SharingService(
            fakes.links, fakes.filters, fakes.sources, fakes.feeds, ImageService(fakes.images)
        ),
        backup=BackupService(
            fakes.links, fakes.sources, fakes.filters, fakes.feeds, fakes.settings, ImageService(fakes.images)
        ),
        updates=UpdateService(fakes.releases, current="0.2.0"),
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>ok</h1>")
    app = create_app(services, web_dir=web, images_dir=tmp_path / "images")
    fakes.client = TestClient(app)
    return fakes


def add_source(world, project="p"):
    return world.client.post(
        "/api/log-sources", json={"name": project, "config": {"project": project}}
    ).json()


# ----- app shell -------------------------------------------------------------


def test_health_reports_each_provider(world):
    add_source(world)
    assert world.client.get("/api/health").json() == {"status": "ok", "logs": True, "github": True}
    world.logs.available = False
    assert world.client.get("/api/health").json()["logs"] is False


def test_static_frontend_is_served_after_the_api_with_no_cache(world):
    page = world.client.get("/")
    assert page.status_code == 200 and "<h1>ok</h1>" in page.text
    assert world.client.get("/api/health").headers["cache-control"] == "no-cache"


# ----- links -----------------------------------------------------------------


def test_links_crud_and_error_mapping(world):
    client = world.client
    created = client.post("/api/links", json={"title": " Console ", "url": "https://x"})
    assert created.status_code == 201
    link = created.json()
    assert link["title"] == "Console" and link["id"]

    assert client.post("/api/links", json={"title": "", "url": "https://x"}).status_code == 422
    missing_url = client.post("/api/links", json={"title": "ok"})
    assert missing_url.status_code == 422
    assert missing_url.json()["detail"] == "URL is required"

    assert [item["id"] for item in client.get("/api/links").json()] == [link["id"]]
    assert client.put("/api/links/ghost", json={"title": "a", "url": "b"}).status_code == 404
    assert client.delete(f"/api/links/{link['id']}").status_code == 204
    assert client.delete(f"/api/links/{link['id']}").status_code == 404


def test_links_order_route_wins_over_the_id_route(world):
    client = world.client
    ids = [client.post("/api/links", json={"title": t, "url": "u"}).json()["id"] for t in "abc"]
    response = client.put("/api/links/order", json={"ids": [ids[2], ids[0], ids[1]]})
    assert response.status_code == 204  # not a 404 for a link called "order"
    assert [item["id"] for item in client.get("/api/links").json()] == [ids[2], ids[0], ids[1]]


def test_run_command_link(world):
    client = world.client
    command = client.post("/api/links", json={"title": "IGV", "kind": "command", "command": "igv.sh"}).json()
    url = client.post("/api/links", json={"title": "Console", "url": "https://x"}).json()

    assert client.post(f"/api/links/{command['id']}/run").json() == {"message": "Program started"}
    assert world.runner.ran == ["igv.sh"]
    assert client.post(f"/api/links/{url['id']}/run").status_code == 422
    assert client.post("/api/links/ghost/run").status_code == 404

    world.runner.error = CommandError("did not open")
    failed = client.post(f"/api/links/{command['id']}/run")
    assert failed.status_code == 502 and failed.json()["detail"] == "did not open"


# ----- logs ------------------------------------------------------------------


def test_log_sources_crud(world):
    client = world.client
    assert client.get("/api/health").json()["logs"] is None  # no sources yet: nothing to check
    created = client.post("/api/log-sources", json={"name": "prod", "config": {"project": "p-prod"}})
    assert created.status_code == 201
    source = created.json()
    assert (source["kind"], source["config"]) == ("gcp", {"project": "p-prod"})
    assert client.get("/api/health").json()["logs"] is True

    assert client.post("/api/log-sources", json={"name": "x", "config": {}}).status_code == 422
    assert client.post("/api/log-sources", json={"name": "x", "kind": "aws", "config": {}}).status_code == 422
    assert (
        client.post("/api/log-sources", json={"name": "PROD", "config": {"project": "q"}}).status_code == 422
    )

    renamed = client.put(
        f"/api/log-sources/{source['id']}", json={"name": "live", "config": {"project": "q"}}
    )
    assert renamed.json()["name"] == "live" and renamed.json()["config"] == {"project": "q"}
    assert (
        client.put("/api/log-sources/ghost", json={"name": "a", "config": {"project": "q"}}).status_code
        == 404
    )

    client.post("/api/filters", json={"name": "a", "source_id": source["id"]})
    in_use = client.delete(f"/api/log-sources/{source['id']}")
    assert in_use.status_code == 422 and "1 filter still use 'live'" in in_use.json()["detail"]
    other = add_source(world, "other")
    assert client.delete(f"/api/log-sources/{other['id']}").status_code == 204
    assert [item["name"] for item in client.get("/api/log-sources").json()] == ["live"]


def test_filters_crud_and_order_route(world):
    client = world.client
    source = add_source(world)["id"]
    first = client.post("/api/filters", json={"name": "a", "source_id": source, "expression": "e"}).json()
    second = client.post("/api/filters", json={"name": "b", "source_id": source}).json()
    assert client.post("/api/filters", json={"name": "x", "source_id": ""}).status_code == 422
    assert client.post("/api/filters", json={"name": "x", "source_id": "ghost"}).status_code == 422
    assert client.put("/api/filters/order", json={"ids": [second["id"], first["id"]]}).status_code == 204
    assert [f["name"] for f in client.get("/api/filters").json()] == ["b", "a"]
    updated = client.put(f"/api/filters/{first['id']}", json={"name": "c", "source_id": source}).json()
    assert updated["name"] == "c" and updated["source_id"] == source
    assert client.delete(f"/api/filters/{first['id']}").status_code == 204
    assert client.put("/api/filters/ghost", json={"name": "c", "source_id": source}).status_code == 404


def test_watched_filters_report_new_entries_until_opened(world):
    client = world.client
    source = add_source(world)["id"]
    saved = client.post("/api/filters", json={"name": "a", "source_id": source}).json()
    assert saved["watch"] is False and saved["last_seen_at"] is None
    assert client.get("/api/filters/unseen").json() == []  # beats /api/filters/{id}

    watched = client.put(f"/api/filters/{saved['id']}/watch", json={"watch": True}).json()
    assert watched["watch"] is True and watched["last_seen_at"] == "2026-10-31T12:00:00Z"
    world.logs.entries = [LogEntry(timestamp="2026-10-31T12:01:00Z", severity="WARNING", summary="w")]
    world.now += timedelta(minutes=3)
    [status] = client.get("/api/filters/unseen").json()
    assert (status["filter_id"], status["warning"], status["total"], status["capped"]) == (
        saved["id"],
        1,
        1,
        False,
    )
    assert (status["since"], status["until"]) == ("2026-10-31T12:00:00Z", "2026-10-31T12:02:00Z")

    world.now += timedelta(minutes=1)
    assert client.post(f"/api/filters/{saved['id']}/seen").json()["last_seen_at"] == "2026-10-31T12:04:00Z"
    assert client.get("/api/filters/unseen").json()[0]["total"] == 0
    # the watch state is the user's own: it does not travel in shared files
    bundle = client.post("/api/share/export", json={"filters": [saved["id"]]}).json()
    assert "watch" not in bundle["log_filters"][0] and "last_seen_at" not in bundle["log_filters"][0]

    world.now += timedelta(minutes=1)
    up_to = client.post(f"/api/filters/{saved['id']}/seen", json={"seen_at": "2026-10-31T12:04:30Z"})
    assert up_to.json()["last_seen_at"] == "2026-10-31T12:04:30Z"

    assert client.put("/api/filters/ghost/watch", json={"watch": True}).status_code == 404
    assert client.post("/api/filters/ghost/seen").status_code == 404


def test_log_query_saved_refined_ad_hoc_and_failures(world):
    client = world.client
    source = add_source(world, "proj-x")["id"]
    saved = client.post(
        "/api/filters",
        json={"name": "a", "source_id": source, "expression": "e", "freshness": "6h", "limit": 25},
    ).json()
    result = client.post(f"/api/filters/{saved['id']}/query").json()
    assert result["command"] == "fake --project=proj-x"
    assert result["console_url"] == "https://console.example/proj-x"
    assert (result["source_id"], result["expression"]) == (source, "e")
    assert [e["summary"] for e in result["entries"]] == ["boom"]
    assert (world.logs.last_query.freshness, world.logs.last_query.limit) == ("6h", 25)

    refined = client.post(
        f"/api/filters/{saved['id']}/query",
        json={"refinements": [{"path": ["httpRequest", "status"], "value": 500, "negate": True}]},
    ).json()
    assert refined["expression"] == "e AND httpRequest.status!=500"
    [refinement] = world.logs.last_query.refinements
    assert (refinement.path, refinement.value, refinement.negate) == (("httpRequest", "status"), 500, True)
    bad = client.post(f"/api/filters/{saved['id']}/query", json={"refinements": [{"path": [], "value": "x"}]})
    assert bad.status_code == 422

    # the new entries of a watched filter: an explicit range and bands
    span = {"since": "2026-10-29T14:00:00Z", "until": "2026-10-31T12:00:00Z"}
    client.post(f"/api/filters/{saved['id']}/query", json={**span, "bands": ["warning", "error"]})
    assert world.logs.last_query.bands == (SeverityBand.ERROR, SeverityBand.WARNING)
    assert world.logs.last_query.time_range.start.isoformat() == "2026-10-29T14:00:00+00:00"
    half = client.post(f"/api/filters/{saved['id']}/query", json={"since": span["since"]})
    backwards = client.post(
        f"/api/filters/{saved['id']}/query", json={"since": span["until"], "until": span["since"]}
    )
    unknown = client.post(f"/api/filters/{saved['id']}/query", json={"bands": ["fatal"]})
    assert (half.status_code, backwards.status_code, unknown.status_code) == (422, 422, 422)

    assert client.post("/api/filters/ghost/query").status_code == 404
    ad_hoc = client.post("/api/logs/query", json={"source_id": source, "expression": "x"})
    assert ad_hoc.status_code == 200 and world.logs.last_query.expression == "x"
    assert client.post("/api/logs/query", json={"source_id": "ghost", "expression": "x"}).status_code == 404

    world.logs.error = LogProviderError("gcloud fora")
    failed = client.post("/api/logs/query", json={"source_id": source, "expression": ""})
    assert failed.status_code == 502 and failed.json()["detail"] == "gcloud fora"


# ----- github ----------------------------------------------------------------


def test_github_feeds_crud_seen_and_fixed_routes(world):
    client = world.client
    created = client.post(
        "/api/github/feeds", json={"name": "Core", "org": "acme", "repos": ["core", "acme/api"]}
    )
    assert created.status_code == 201
    feed = created.json()
    assert feed["repos"] == ["core", "api"] and feed["last_seen_at"].endswith("Z")
    assert client.post("/api/github/feeds", json={"name": "x", "org": "not an org"}).status_code == 422

    other = client.post("/api/github/feeds", json={"name": "Org", "org": "acme"}).json()
    assert client.put("/api/github/feeds/order", json={"ids": [other["id"], feed["id"]]}).status_code == 204
    assert [f["name"] for f in client.get("/api/github/feeds").json()] == ["Org", "Core"]

    seen = client.post(f"/api/github/feeds/{feed['id']}/seen", json={"seen_at": "2099-01-01T00:00:00Z"})
    assert seen.json()["last_seen_at"] == "2099-01-01T00:00:00Z"
    assert client.post(f"/api/github/feeds/{feed['id']}/seen", json={"seen_at": "ontem"}).status_code == 422

    world.github.org["acme"] = [make_event(1, at="2100-01-01T00:00:00Z")]
    unseen = client.get("/api/github/feeds/unseen")
    assert unseen.status_code == 200 and unseen.json()[other["id"]] == 1

    assert (
        client.put(f"/api/github/feeds/{feed['id']}", json={"name": "C2", "org": "acme"}).json()["name"]
        == "C2"
    )
    assert client.delete(f"/api/github/feeds/{feed['id']}").status_code == 204
    assert client.delete(f"/api/github/feeds/{feed['id']}").status_code == 404


def test_github_events_query_and_repos(world):
    client = world.client
    world.github.org["acme"] = [make_event(1, "pr", at="2026-10-22T10:00:00Z")]
    feed = client.post("/api/github/feeds", json={"name": "Org", "org": "acme"}).json()

    by_feed = client.post(f"/api/github/feeds/{feed['id']}/query").json()
    assert [e["id"] for e in by_feed["events"]] == ["1"] and by_feed["viewer"] == "alice"
    assert by_feed["unseen"] == 0 and by_feed["events"][0]["is_new"] is False  # created "caught up"
    world.github.org["acme"].append(make_event(9, "pr", at="2100-01-01T00:00:00Z"))
    fresh = client.post(f"/api/github/feeds/{feed['id']}/query").json()
    assert fresh["unseen"] == 1 and [e["is_new"] for e in fresh["events"]] == [True, False]
    # the details value object is serialized as a plain object the UI can read
    world.github.org["acme"].append(make_push(2, "2026-10-22T11:00:00Z"))
    events = client.post(f"/api/github/feeds/{feed['id']}/query").json()["events"]
    push = next(e for e in events if e["id"] == "2")
    assert (push["kind"], push["source"], push["is_pull"]) == ("push", "events", False)
    assert push["details"] == {"before": "b", "head": "h", "heads": ["h"], "commits": []}
    assert client.post("/api/github/feeds/ghost/query").status_code == 404
    assert client.post("/api/github/events/query", json={}).status_code == 422  # org is required

    ad_hoc = client.post("/api/github/events/query", json={"org": "acme", "event_kinds": ["pr"]})
    assert ad_hoc.status_code == 200 and len(ad_hoc.json()["events"]) == 2  # the PRs, not the push
    assert ad_hoc.json()["unseen"] == 0  # ad hoc: no mark to compare with
    assert client.post("/api/github/events/query", json={"org": "bad org"}).status_code == 422

    world.github.repos[("acme", "core")] = GithubError("GitHub token is invalid")
    failed = client.post("/api/github/events/query", json={"org": "acme", "repos": ["core"]})
    assert failed.status_code == 502 and "token" in failed.json()["detail"]

    preview = client.post(
        "/api/github/feeds/preview",
        json={
            "org": "acme",
            "repos": ["core", "acme/api", "https://github.com/acme/web.git", "CORE"],
            "event_kinds": ["review", "pr"],
        },
    )
    assert preview.status_code == 200
    assert preview.json() == {
        "org": "acme",
        "repos": ["core", "api", "web"],
        "event_kinds": ["pr", "review"],
        "hide_own": False,
        "hide_bots": True,
    }
    bad = client.post("/api/github/feeds/preview", json={"org": "acme", "repos": ["other/x"]})
    assert bad.status_code == 422 and "does not belong" in bad.json()["detail"]

    assert client.get("/api/github/orgs/acme/repos").json() == []
    assert client.get("/api/github/orgs/bad%20org/repos").status_code == 422


# ----- settings & images -----------------------------------------------------


def test_settings_roundtrip(world):
    client = world.client
    defaults = client.get("/api/settings").json()
    assert (defaults["theme"], defaults["polling"]) == ("midnight", True)
    assert (defaults["logs_every"], defaults["github_every"], defaults["feed_refresh_every"]) == (
        120,
        300,
        60,
    )
    assert defaults["every_choices"]["feed_refresh_every"] == [30, 60, 120, 300]  # for the menus
    saved = client.put("/api/settings", json={"theme": "paper"}).json()
    assert (saved["theme"], saved["polling"]) == ("paper", True)
    assert world.settings.load().theme == "paper"


def test_settings_update_is_partial(world):
    client = world.client
    client.put("/api/settings", json={"theme": "paper"})
    paused = client.put("/api/settings", json={"polling": False}).json()
    assert (paused["theme"], paused["polling"]) == ("paper", False)
    # saving the theme alone, as the theme picker does, keeps polling paused
    assert client.put("/api/settings", json={"theme": "ember"}).json()["polling"] is False


def test_settings_intervals_take_only_the_offered_choices(world):
    client = world.client
    assert client.put("/api/settings", json={"logs_every": 600}).json()["logs_every"] == 600
    bad = client.put("/api/settings", json={"logs_every": 5, "theme": "paper"})
    assert bad.status_code == 422 and "logs_every" in bad.json()["detail"]
    kept = world.settings.load()
    assert (kept.logs_every, kept.theme) == (600, "midnight")  # nothing changed


def test_backup_download_check_and_restore(world):
    client = world.client
    client.post("/api/links", json={"title": "Console", "url": "https://console.cloud.google.com"})
    client.put("/api/settings", json={"theme": "paper"})

    download = client.get("/api/backup")
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]  # named after the day it was made
    assert disposition.startswith('attachment; filename="opsdeck-backup-') and disposition.endswith('.json"')
    backup = download.json()
    assert backup["opsdeck_backup"] == 1 and backup["settings"]["theme"] == "paper"

    client.post("/api/links", json={"title": "Later", "url": "https://later.example"})
    client.put("/api/settings", json={"theme": "ember"})
    check = client.post("/api/backup/check", json=backup).json()
    assert (check["links"], check["log_filters"]) == (1, 0)
    assert len(world.links.list_all()) == 2  # a check writes nothing

    restored = client.post("/api/backup/restore", json=backup)
    assert restored.status_code == 200 and restored.json()["links"] == 1
    assert [link.title for link in world.links.list_all()] == ["Console"]
    assert world.settings.load().theme == "paper"

    bad = client.post("/api/backup/restore", json={"opsdeck": 2, "links": []})
    assert bad.status_code == 422 and "export of chosen items" in bad.json()["detail"]
    assert client.post("/api/backup/check", json=[1, 2]).status_code == 422


def test_image_upload(world):
    client = world.client
    ok = client.post("/api/images", files={"file": ("foto.PNG", b"\x89PNG", "image/png")})
    assert ok.status_code == 201 and ok.json() == {"url": "/images/1.png"}
    bad = client.post("/api/images", files={"file": ("run.sh", b"#!", "text/plain")})
    assert bad.status_code == 422 and "Unsupported format" in bad.json()["detail"]


def test_unexpected_errors_return_a_readable_reason(world):
    world.logs.error = OSError("Project was not passed and could not be determined")
    source = add_source(world)["id"]
    saved = world.client.post("/api/filters", json={"name": "a", "source_id": source}).json()
    response = TestClient(world.client.app, raise_server_exceptions=False).post(
        f"/api/filters/{saved['id']}/query"
    )
    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == "Unexpected server error (OSError): Project was not passed and could not be determined"
    )


def test_github_token_status_route(world):
    status = world.client.get("/api/github/token").json()
    assert status == {
        "kind": "other",
        "login": "",
        "scopes": None,
        "usable": True,
        "problems": [],
        "notes": [],
    }


def test_github_event_context_route(world):
    client = world.client
    subject = EventSubject(SubjectType.REVIEW_COMMENT, "acme/core", item_id=5)
    world.github.contexts[subject] = EventContext(
        subject=subject, author="bob", body_html="<p>full</p>", path="a.py", line=3, diff_hunk="@@ +1 @@"
    )
    body = {"type": "review_comment", "repo": "acme/core", "item_id": 5}
    ok = client.post("/api/github/events/context", json=body)
    assert ok.status_code == 200
    assert ok.json()["body_html"] == "<p>full</p>" and ok.json()["subject"] == {**body, "number": 0}
    assert (ok.json()["path"], ok.json()["line"], ok.json()["diff_hunk"]) == ("a.py", 3, "@@ +1 @@")
    gone = client.post("/api/github/events/context", json={**body, "item_id": 6})
    assert gone.status_code == 404 and "no longer exists" in gone.json()["detail"]
    assert client.post("/api/github/events/context", json={**body, "item_id": 0}).status_code == 422
    assert client.post("/api/github/events/context", json={**body, "repo": "acme/.."}).status_code == 422
    assert client.post("/api/github/events/context", json={**body, "type": "wiki"}).status_code == 422


def test_github_events_expose_their_subject(world):
    client = world.client
    world.github.org["acme"] = [make_code_comment(1, "2026-10-22T10:00:00Z")]
    events = client.post("/api/github/events/query", json={"org": "acme"}).json()["events"]
    assert events[0]["subject"] == {"type": "review_comment", "repo": "acme/core", "number": 0, "item_id": 2}


def test_github_event_context_carries_the_thread_and_pull_stats(world):
    client = world.client
    comment = EventSubject(SubjectType.REVIEW_COMMENT, "acme/core", item_id=5)
    world.github.contexts[comment] = EventContext(
        subject=comment, thread=(ThreadComment(id=4, author="bob"), ThreadComment(id=5, author="carol"))
    )
    pull = EventSubject(SubjectType.PULL, "acme/core", number=12)
    world.github.contexts[pull] = EventContext(
        subject=pull,
        pull=PullStats(
            additions=3, labels=(Label("bug", "d73a4a"),), reviewers=(Reviewer("bob", ReviewState.APPROVED),)
        ),
    )
    thread = client.post(
        "/api/github/events/context", json={"type": "review_comment", "repo": "acme/core", "item_id": 5}
    )
    assert [c["id"] for c in thread.json()["thread"]] == [4, 5] and thread.json()["pull"] is None
    stats = client.post(
        "/api/github/events/context", json={"type": "pull", "repo": "acme/core", "number": 12}
    ).json()
    assert stats["pull"]["additions"] == 3 and stats["pull"]["labels"] == [{"name": "bug", "color": "d73a4a"}]
    assert stats["pull"]["reviewers"] == [{"login": "bob", "state": "approved"}]


def test_github_feed_contributions_route(world):
    client = world.client
    feed = client.post("/api/github/feeds", json={"name": "Org", "org": "acme"}).json()
    world.github.contributed = [Contribution("bob", ContributionKind.PR, "acme/core", utc_now(), "")]
    world.github.quota = Quota(remaining=4000, limit=5000)

    body = client.post(f"/api/github/feeds/{feed['id']}/contributions", json={"days": 7, "tz": "UTC"}).json()
    assert body["counts"] == [
        {"login": "bob", "day": body["until"], "kind": "pr", "repo": "acme/core", "count": 1}
    ]
    assert (body["rate_remaining"], body["rate_limit"], body["viewer"]) == (4000, 5000, "alice")
    url = f"/api/github/feeds/{feed['id']}/contributions"
    assert client.post(url, json={"days": 7, "tz": "Nowhere/Here"}).status_code == 422
    assert client.post("/api/github/feeds/ghost/contributions", json={}).status_code == 404
    world.github.contributed = GithubError("GitHub GraphQL failed: boom")
    assert client.post(url, json={}).status_code == 502


def test_github_feed_contribution_day_route(world):
    client = world.client
    feed = client.post("/api/github/feeds", json={"name": "Org", "org": "acme"}).json()
    review = Contribution(
        "ana",
        ContributionKind.REVIEW,
        "acme/core",
        utc_now(),
        number=7,
        title="Add it",
        url="https://github.com/acme/core/pull/7#pullrequestreview-1",
        review_state=ReviewState.APPROVED,
    )
    world.github.contributed = [review]
    url = f"/api/github/feeds/{feed['id']}/contributions/day"
    today = client.post(f"/api/github/feeds/{feed['id']}/contributions", json={"days": 7}).json()["until"]

    (item,) = client.post(url, json={"days": 7, "login": "ana", "day": today}).json()
    assert (item["kind"], item["number"], item["title"], item["review_state"]) == (
        "review",
        7,
        "Add it",
        "approved",
    )
    assert client.post(url, json={"days": 7, "login": "ana", "day": "2000-01-01"}).status_code == 422
    assert client.post(url, json={"days": 7, "day": today}).status_code == 422  # login is required


# ----- sharing ---------------------------------------------------------------


def test_share_export_check_and_import_round_trip(world):
    client = world.client
    source = add_source(world)["id"]
    created = client.post("/api/filters", json={"name": "errors", "source_id": source}).json()
    bundle = client.post("/api/share/export", json={"filters": [created["id"]]}).json()
    assert bundle["opsdeck"] == 2
    assert bundle["log_filters"][0]["source"] == {"name": "p", "kind": "gcp", "config": {"project": "p"}}
    assert bundle["log_filters"][0]["name"] == "errors"
    assert "id" not in bundle["log_filters"][0]

    checks = client.post("/api/share/check", json=bundle).json()
    assert checks == [
        {
            "type": "filter",
            "index": 0,
            "name": "errors",
            "detail": "p",
            "status": "conflict",
            "error": "",
            "command": "",
        }
    ]

    choice = {"type": "filter", "index": 0, "action": "create"}
    result = client.post("/api/share/import", json={"bundle": bundle, "choices": [choice]}).json()
    assert result["replaced"] == [] and result["created"][0]["type"] == "filter"
    assert [f["name"] for f in client.get("/api/filters").json()] == ["errors", "errors (2)"]


def test_share_errors_map_to_http(world):
    client = world.client
    assert client.post("/api/share/export", json={"links": ["nope"]}).status_code == 404
    not_export = client.post("/api/share/check", json={"links": []})
    assert not_export.status_code == 422
    assert not_export.json()["detail"] == "Not an OpsDeck export"
    bad = {"opsdeck": 1, "links": [{"title": "x"}]}
    choice = {"type": "link", "index": 0, "action": "create"}
    rejected = client.post("/api/share/import", json={"bundle": bad, "choices": [choice]})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "Link 'x': URL is required"


def test_version_without_a_release(world):
    assert world.client.get("/api/version").json() == {
        "current": "0.2.0",
        "latest": None,
        "newer": False,
        "url": None,
        "published_at": None,
    }


def test_version_with_a_release(tmp_path):
    feed = FakeReleaseFeed(Release("0.3.0", "https://github.com/lmtani/opsdeck/releases/tag/v0.3.0"))
    services = SimpleNamespace(updates=UpdateService(feed, current="0.2.0"))
    web = tmp_path / "web"
    web.mkdir()
    client = TestClient(create_app(services, web_dir=web, images_dir=tmp_path / "images"))  # type: ignore[arg-type]
    body = client.get("/api/version").json()
    assert (body["latest"], body["newer"]) == ("0.3.0", True)
    assert body["url"].endswith("/v0.3.0")
