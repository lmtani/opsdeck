"""Sharing use cases: export to a bundle, check it, import it. In-memory fakes only."""

import base64
from types import SimpleNamespace

import pytest

from opsdeck.application.github import GithubFeedService
from opsdeck.application.links import ImageService, LinkService
from opsdeck.application.logs import LogFilterService, LogSourceService
from opsdeck.application.sharing import (
    Bundle,
    ImportAction,
    ImportChoice,
    ImportStatus,
    ItemType,
    SharingService,
)
from opsdeck.domain.errors import NotFoundError, ValidationError
from tests.fakes import (
    FakeImageStore,
    InMemoryFeedRepository,
    InMemoryFilterRepository,
    InMemoryLinkRepository,
    InMemorySourceRepository,
)

PNG = b"\x89PNG fake"


def make_deck():
    links, filters, feeds = InMemoryLinkRepository(), InMemoryFilterRepository(), InMemoryFeedRepository()
    sources = InMemorySourceRepository()
    images = ImageService(FakeImageStore())
    return SimpleNamespace(
        links=LinkService(links),
        sources=LogSourceService(sources, filters),
        filters=LogFilterService(filters, sources),
        feeds=GithubFeedService(feeds),
        images=images,
        sharing=SharingService(links, filters, sources, feeds, images),
    )


def gcp(deck, project, name=None):
    return deck.sources.create_source(name or project, "gcp", {"project": project}).id


@pytest.fixture
def deck():
    return make_deck()


def create(item_type, index=0, action=ImportAction.CREATE):
    return ImportChoice(item_type, index, action)


def test_export_keeps_only_portable_fields_in_the_users_order(deck):
    first = deck.filters.create_filter(name="errors", source_id=gcp(deck, "p1"), expression="severity>=ERROR")
    second = deck.filters.create_filter(name="api", source_id=gcp(deck, "p2", "Billing"), limit=20)
    feed = deck.feeds.create_feed(name="core", org="acme", repos=["core"], event_kinds=["pr"])
    bundle = deck.sharing.export([], [second.id, first.id], [feed.id])
    assert [item["name"] for item in bundle.log_filters] == ["errors", "api"]
    assert bundle.log_filters[1] == {
        "name": "api",
        "source": {"name": "Billing", "kind": "gcp", "config": {"project": "p2"}},
        "expression": "",
        "freshness": "1h",
        "limit": 20,
        "description": "",
        "line_format": "",
    }
    assert set(bundle.github_feeds[0]) == {
        "name",
        "org",
        "repos",
        "event_kinds",
        "hide_own",
        "hide_bots",
        "description",
    }
    assert bundle.links == [] and bundle.exported_at is not None


def test_export_rejects_an_unknown_id(deck):
    with pytest.raises(NotFoundError):
        deck.sharing.export(["nope"], [], [])


def test_export_embeds_uploaded_images_and_keeps_external_urls(deck):
    uploaded = deck.images.save_image(PNG, "logo.png")
    a = deck.links.create_link(title="a", url="https://a", image_url=uploaded)
    b = deck.links.create_link(title="b", url="https://b", image_url="https://cdn/b.png")
    bundle = deck.sharing.export([a.id, b.id], [], [])
    assert bundle.links[0]["image_url"] == ""
    assert bundle.links[0]["image"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert bundle.links[1]["image_url"] == "https://cdn/b.png"
    assert "image" not in bundle.links[1]


def test_round_trip_into_another_deck_saves_the_embedded_image(deck):
    uploaded = deck.images.save_image(PNG, "logo.png")
    link = deck.links.create_link(title="grafana", url="https://g", image_url=uploaded, category="obs")
    bundle = deck.sharing.export([link.id], [], [])

    other = make_deck()
    result = other.sharing.import_bundle(bundle, [create(ItemType.LINK)])
    [imported] = other.links.list_links()
    assert result.created == [(ItemType.LINK, imported.id)]
    assert (imported.title, imported.url, imported.category) == ("grafana", "https://g", "obs")
    assert other.images.load_image(imported.image_url) == (PNG, "png")


def test_check_reports_new_conflicts_and_invalid_items(deck):
    deck.filters.create_filter(name="Errors", source_id=gcp(deck, "p"))
    bundle = Bundle(
        log_filters=[
            {"name": "errors ", "project": "p"},  # same name, other case and spaces
            {"name": "fresh", "project": "p"},
            {"name": "broken", "project": ""},
            {"name": "bad limit", "project": "p", "limit": "50"},
        ],
        github_feeds=[{"name": "f", "org": "acme", "event_kinds": ["nope"]}],
    )
    checks = deck.sharing.check(bundle)
    assert [(c.type, c.index, c.status) for c in checks] == [
        (ItemType.FILTER, 0, ImportStatus.CONFLICT),
        (ItemType.FILTER, 1, ImportStatus.NEW),
        (ItemType.FILTER, 2, ImportStatus.INVALID),
        (ItemType.FILTER, 3, ImportStatus.INVALID),
        (ItemType.FEED, 0, ImportStatus.INVALID),
    ]
    assert checks[2].error == "Project is required"
    assert checks[3].error == "Field 'limit' must be a number"
    assert checks[1].detail == "p"


def test_check_shows_the_command_of_a_command_card(deck):
    bundle = Bundle(links=[{"title": "deploy", "kind": "command", "command": "make deploy"}])
    [check] = deck.sharing.check(bundle)
    assert check.status is ImportStatus.NEW
    assert check.command == "make deploy" and check.detail == "make deploy"


def test_check_rejects_a_file_that_is_not_an_export(deck):
    with pytest.raises(ValidationError, match="Not an OpsDeck export"):
        deck.sharing.check(Bundle(version=0))
    with pytest.raises(ValidationError, match="newer OpsDeck"):
        deck.sharing.check(Bundle(version=3))


def test_import_keep_both_renames_and_appends(deck):
    existing = deck.feeds.create_feed(name="core", org="acme")
    bundle = Bundle(github_feeds=[{"name": "core", "org": "acme", "repos": ["web"]}] * 2)
    deck.sharing.import_bundle(bundle, [create(ItemType.FEED, 0), create(ItemType.FEED, 1)])
    feeds = deck.feeds.list_feeds()
    assert [f.name for f in feeds] == ["core", "core (2)", "core (3)"]
    assert feeds[0] is existing and feeds[1].repos == ["web"]
    assert feeds[1].last_seen_at is not None  # starts caught up, like a new feed


def test_import_replace_overwrites_fields_and_keeps_identity(deck):
    existing = deck.filters.create_filter(name="errors", source_id=gcp(deck, "old"))
    deck.filters.create_filter(name="other", source_id=gcp(deck, "x"))
    bundle = Bundle(log_filters=[{"name": "Errors", "project": "new", "limit": 10}])  # a version 1 file
    result = deck.sharing.import_bundle(bundle, [create(ItemType.FILTER, 0, ImportAction.REPLACE)])
    assert result.replaced == [(ItemType.FILTER, existing.id)] and result.created == []
    [replaced, _] = deck.filters.list_filters()
    assert (replaced.id, replaced.position) == (existing.id, existing.position)
    assert (replaced.name, replaced.limit) == ("Errors", 10)
    assert deck.sources.list_sources()[0].name == "new"  # the project became a source
    assert replaced.source_id == deck.sources.list_sources()[0].id


def test_import_skips_items_left_out_of_the_choices(deck):
    bundle = Bundle(log_filters=[{"name": "a", "project": "p"}, {"name": "b", "project": "p"}])
    deck.sharing.import_bundle(bundle, [create(ItemType.FILTER, 1)])
    assert [f.name for f in deck.filters.list_filters()] == ["b"]


def test_a_bad_choice_imports_nothing(deck):
    bundle = Bundle(log_filters=[{"name": "ok", "project": "p"}, {"name": "bad", "project": ""}])
    with pytest.raises(ValidationError, match="Filter 'bad': Project is required"):
        deck.sharing.import_bundle(bundle, [create(ItemType.FILTER, 0), create(ItemType.FILTER, 1)])
    with pytest.raises(ValidationError, match="nothing to replace"):
        deck.sharing.import_bundle(bundle, [create(ItemType.FILTER, 0, ImportAction.REPLACE)])
    with pytest.raises(ValidationError, match="no link #1"):
        deck.sharing.import_bundle(bundle, [create(ItemType.LINK, 0)])
    assert deck.filters.list_filters() == [] and deck.sources.list_sources() == []


def source_item(name, project, kind="gcp"):
    return {"name": name, "kind": kind, "config": {"project": project}}


def test_a_filter_reuses_the_source_that_reads_the_same_place(deck):
    here = gcp(deck, "proj-prod", "Production")
    bundle = Bundle(log_filters=[{"name": "errors", "source": source_item("Prod", "proj-prod")}])
    [check] = deck.sharing.check(bundle)
    assert (check.status, check.detail) == (ImportStatus.NEW, "Production")
    deck.sharing.import_bundle(bundle, [create(ItemType.FILTER)])
    assert deck.filters.list_filters()[0].source_id == here
    assert len(deck.sources.list_sources()) == 1


def test_a_missing_source_is_created_once_under_a_free_name(deck):
    gcp(deck, "proj-other", "Prod")  # the name is taken by another project
    bundle = Bundle(
        log_filters=[
            {"name": "errors", "source": source_item("Prod", "proj-prod")},
            {"name": "slow", "source": source_item("Prod", "proj-prod")},
        ]
    )
    checks = deck.sharing.check(bundle)
    assert [check.detail for check in checks] == ["Prod · new log source"] * 2
    deck.sharing.import_bundle(bundle, [create(ItemType.FILTER, 0), create(ItemType.FILTER, 1)])

    created = [source for source in deck.sources.list_sources() if source.config == {"project": "proj-prod"}]
    assert [source.name for source in created] == ["Prod (2)"]
    assert {f.source_id for f in deck.filters.list_filters()} == {created[0].id}


@pytest.mark.parametrize(
    ("item", "error"),
    [
        ({"name": "a"}, "The filter has no log source"),
        ({"name": "a", "source": "proj"}, "The filter has no log source"),
        (
            {"name": "a", "source": {"name": "x", "kind": "gcp"}},
            "The filter's log source needs a name, a kind and a config",
        ),
        ({"name": "a", "source": source_item("x", "p", kind="aws")}, "Unknown kind of log source: 'aws'"),
    ],
)
def test_a_filter_with_a_bad_source_is_invalid(deck, item, error):
    [check] = deck.sharing.check(Bundle(log_filters=[item]))
    assert (check.status, check.error) == (ImportStatus.INVALID, error)


def test_import_drops_an_upload_path_without_its_image(deck):
    bundle = Bundle(links=[{"title": "a", "url": "https://a", "image_url": "/images/gone.png"}])
    deck.sharing.import_bundle(bundle, [create(ItemType.LINK)])
    assert deck.links.list_links()[0].image_url == ""


def test_import_rejects_a_bad_embedded_image(deck):
    bundle = Bundle(
        links=[
            {"title": "a", "url": "https://a", "image": "data:image/bmp;base64,AAAA"},
            {"title": "b", "url": "https://b", "image": "data:image/png;base64,***"},
        ]
    )
    errors = [check.error for check in deck.sharing.check(bundle)]
    assert errors == ["Unsupported image type", "Image is not valid base64"]
