"""HTTP layer over in-memory fakes: status codes, error mapping and the
route precedence that depends on declaration order (/order, /unseen)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.application.github import GithubActivityService, GithubFeedService
from app.application.links import CommandService, ImageService, LinkService
from app.application.logs import LogFilterService, LogQueryService
from app.application.settings import SettingsService
from app.domain.errors import CommandError, GithubError, LogProviderError
from app.presentation.api import Services, create_app
from tests.fakes import (
    FakeCommandRunner,
    FakeGithubProvider,
    FakeImageStore,
    FakeLogProvider,
    InMemoryFeedRepository,
    InMemoryFilterRepository,
    InMemoryLinkRepository,
    InMemorySettingsRepository,
    make_event,
    make_push,
)


@pytest.fixture()
def world(tmp_path):
    fakes = SimpleNamespace(
        links=InMemoryLinkRepository(),
        filters=InMemoryFilterRepository(),
        feeds=InMemoryFeedRepository(),
        settings=InMemorySettingsRepository(),
        runner=FakeCommandRunner(),
        logs=FakeLogProvider(),
        github=FakeGithubProvider(),
        images=FakeImageStore(),
    )
    services = Services(
        links=LinkService(fakes.links),
        commands=CommandService(fakes.runner, fakes.links),
        filters=LogFilterService(fakes.filters),
        logs=LogQueryService(fakes.logs, fakes.filters),
        settings=SettingsService(fakes.settings),
        images=ImageService(fakes.images),
        github_feeds=GithubFeedService(fakes.feeds),
        github=GithubActivityService(fakes.github, fakes.feeds),
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>ok</h1>")
    app = create_app(services, web_dir=web, images_dir=tmp_path / "images")
    fakes.client = TestClient(app)
    return fakes


# ----- app shell -------------------------------------------------------------


def test_health_reports_each_provider(world):
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


def test_filters_crud_and_order_route(world):
    client = world.client
    first = client.post("/api/filters", json={"name": "a", "project": "p", "expression": "e"}).json()
    second = client.post("/api/filters", json={"name": "b", "project": "p"}).json()
    assert client.post("/api/filters", json={"name": "x", "project": "", "expression": ""}).status_code == 422
    assert client.put("/api/filters/order", json={"ids": [second["id"], first["id"]]}).status_code == 204
    assert [f["name"] for f in client.get("/api/filters").json()] == ["b", "a"]
    assert client.put(f"/api/filters/{first['id']}", json={"name": "c", "project": "p"}).json()["name"] == "c"
    assert client.delete(f"/api/filters/{first['id']}").status_code == 204
    assert client.put("/api/filters/ghost", json={"name": "c", "project": "p"}).status_code == 404


def test_log_query_saved_ad_hoc_and_failures(world):
    client = world.client
    saved = client.post("/api/filters", json={"name": "a", "project": "proj-x", "expression": "e",
                                              "freshness": "6h", "limit": 25}).json()
    result = client.post(f"/api/filters/{saved['id']}/query").json()
    assert result["command"] == "fake --project=proj-x"
    assert [e["summary"] for e in result["entries"]] == ["boom"]
    assert (world.logs.last_query.freshness, world.logs.last_query.limit) == ("6h", 25)

    assert client.post("/api/filters/ghost/query").status_code == 404
    ad_hoc = client.post("/api/logs/query", json={"project": "p", "expression": "x"})
    assert ad_hoc.status_code == 200 and world.logs.last_query.expression == "x"
    assert client.post("/api/logs/query", json={"project": " ", "expression": "x"}).status_code == 422

    world.logs.error = LogProviderError("gcloud fora")
    failed = client.post("/api/logs/query", json={"project": "p", "expression": ""})
    assert failed.status_code == 502 and failed.json()["detail"] == "gcloud fora"


# ----- github ----------------------------------------------------------------


def test_github_feeds_crud_seen_and_fixed_routes(world):
    client = world.client
    created = client.post("/api/github/feeds", json={"name": "Core", "org": "acme",
                                                     "repos": ["core", "acme/api"]})
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

    assert client.put(f"/api/github/feeds/{feed['id']}", json={"name": "C2", "org": "acme"}).json()["name"] == "C2"
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

    preview = client.post("/api/github/feeds/preview", json={
        "org": "acme", "repos": ["core", "acme/api", "https://github.com/acme/web.git", "CORE"],
        "event_kinds": ["review", "pr"],
    })
    assert preview.status_code == 200
    assert preview.json() == {"org": "acme", "repos": ["core", "api", "web"],
                              "event_kinds": ["pr", "review"], "hide_own": False, "hide_bots": True}
    bad = client.post("/api/github/feeds/preview", json={"org": "acme", "repos": ["other/x"]})
    assert bad.status_code == 422 and "does not belong" in bad.json()["detail"]

    assert client.get("/api/github/orgs/acme/repos").json() == []
    assert client.get("/api/github/orgs/bad%20org/repos").status_code == 422


# ----- settings & images -----------------------------------------------------


def test_settings_roundtrip(world):
    client = world.client
    assert client.get("/api/settings").json() == {"theme": "midnight"}
    assert client.put("/api/settings", json={"theme": "paper"}).json() == {"theme": "paper"}
    assert world.settings.load().theme == "paper"


def test_image_upload(world):
    client = world.client
    ok = client.post("/api/images", files={"file": ("foto.PNG", b"\x89PNG", "image/png")})
    assert ok.status_code == 201 and ok.json() == {"url": "/images/1.png"}
    bad = client.post("/api/images", files={"file": ("run.sh", b"#!", "text/plain")})
    assert bad.status_code == 422 and "Unsupported format" in bad.json()["detail"]


def test_unexpected_errors_return_a_readable_reason(world):
    world.logs.error = OSError("Project was not passed and could not be determined")
    saved = world.client.post("/api/filters", json={"name": "a", "project": "p"}).json()
    response = TestClient(world.client.app, raise_server_exceptions=False).post(f"/api/filters/{saved['id']}/query")
    assert response.status_code == 500
    assert response.json()["detail"] == "Unexpected server error (OSError): Project was not passed and could not be determined"


def test_github_token_status_route(world):
    status = world.client.get("/api/github/token").json()
    assert status == {"kind": "other", "login": "", "scopes": None, "usable": True, "problems": [], "notes": []}
