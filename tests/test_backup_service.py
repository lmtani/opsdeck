"""Backups: everything out to one JSON document and back, on another install."""

import json

import pytest

from opsdeck.application.backup import BackupService
from opsdeck.application.links import ImageService
from opsdeck.domain.errors import ValidationError
from opsdeck.domain.github import GithubFeed
from opsdeck.domain.links import Link
from opsdeck.domain.logs import LogFilter, LogSource
from opsdeck.domain.settings import AppSettings
from tests.fakes import (
    FakeImageStore,
    InMemoryFeedRepository,
    InMemoryFilterRepository,
    InMemoryLinkRepository,
    InMemorySettingsRepository,
    InMemorySourceRepository,
    ts,
)

PNG = b"\x89PNG\r\n\x1a\nimage"


class Install:
    """One OpsDeck's repositories and its backup service."""

    def __init__(self):
        self.links = InMemoryLinkRepository()
        self.sources = InMemorySourceRepository()
        self.filters = InMemoryFilterRepository()
        self.feeds = InMemoryFeedRepository()
        self.settings = InMemorySettingsRepository()
        self.store = FakeImageStore()
        self.images = ImageService(self.store)
        self.backup = BackupService(
            self.links, self.sources, self.filters, self.feeds, self.settings, self.images
        )

    def everything(self):
        return (
            self.links.list_all(),
            self.sources.list_all(),
            self.filters.list_all(),
            self.feeds.list_all(),
            self.settings.load(),
        )


def filled() -> Install:
    install = Install()
    image_url = install.images.save_image(PNG, "cover.png")
    install.links.save(Link.new(title="Console", url="https://console.cloud.google.com", position=2))
    install.links.save(Link.new(title="Wiki", url="https://wiki.example", image_url=image_url))
    source = LogSource.new(name="Prod", kind="gcp", config={"project": "proj-x"})
    install.sources.save(source)
    log_filter = LogFilter.new(
        name="errors", source_id=source.id, expression="severity>=ERROR", freshness="6h"
    )
    log_filter.set_watch(True, ts("2026-10-30T09:00:00Z"))
    install.filters.save(log_filter)
    feed = GithubFeed.new(name="Core", org="acme", repos=["core"], event_kinds=["pr"])
    feed.last_seen_at = ts("2026-10-29T08:00:00Z")  # read up to here
    install.feeds.save(feed)
    install.settings.save(AppSettings(theme="paper", polling=False, logs_every=600))
    return install


def through_a_file(backup: dict) -> dict:
    return json.loads(json.dumps(backup))


def test_a_backup_restores_everything_exactly_on_another_install():
    origin = filled()
    backup = through_a_file(origin.backup.create())
    assert backup["opsdeck_backup"] == 1 and len(backup["images"]) == 1

    target = Install()
    target.images.save_image(b"\x89PNG\r\n\x1a\nother", "x.png")  # its uploads number differently
    stale = Link.new(title="Stale", url="https://old.example")
    target.links.save(stale)
    old_source = LogSource.new(name="Old", kind="gcp", config={"project": "old"})
    target.sources.save(old_source)
    target.filters.save(LogFilter.new(name="old", source_id=old_source.id))

    summary = target.backup.restore(backup)
    assert (
        summary.links,
        summary.log_sources,
        summary.log_filters,
        summary.github_feeds,
        summary.images,
    ) == (
        2,
        1,
        1,
        1,
        1,
    )

    links, sources, filters, feeds, settings = target.everything()
    origin_links, origin_sources, origin_filters, origin_feeds, origin_settings = origin.everything()
    assert sources == origin_sources and filters == origin_filters and feeds == origin_feeds
    assert settings == origin_settings
    assert filters[0].watch and filters[0].last_seen_at == ts("2026-10-30T09:00:00Z")  # personal state kept
    assert feeds[0].last_seen_at == ts("2026-10-29T08:00:00Z")
    by_id = {link.id: link for link in links}
    assert set(by_id) == {link.id for link in origin_links}  # the stale card is gone
    wiki = next(link for link in links if link.title == "Wiki")
    assert wiki.image_url == "/images/2.png" and target.store.load(wiki.image_url) == (PNG, "png")
    console = next(link for link in links if link.title == "Console")
    assert console == next(link for link in origin_links if link.title == "Console")


def test_check_reads_the_file_without_writing():
    backup = through_a_file(filled().backup.create())
    target = Install()
    summary = target.backup.check(backup)
    assert (summary.links, summary.log_filters) == (2, 1) and summary.created_at is not None
    assert target.everything() == Install().everything()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b: b.pop("opsdeck_backup") and b.update(opsdeck=2), "export of chosen items"),
        (lambda b: b.update(opsdeck_backup=99), "newer OpsDeck"),
        (lambda b: b.update(log_sources=[]), "log source is not in the backup"),
        (lambda b: b["links"][0].update(position="first"), "Card 'Console': Field 'position'"),
        (lambda b: b["log_filters"][0].update(freshness="2y"), "Filter 'errors'"),
        (lambda b: b["github_feeds"].append(dict(b["github_feeds"][0])), "repeated id"),
        (lambda b: b["settings"].update(logs_every=7), "Settings"),
        (lambda b: b["images"].update({"/images/1.png": "data:text/plain;base64,eA=="}), "Image"),
    ],
)
def test_an_invalid_backup_is_refused_before_anything_changes(change, message):
    backup = through_a_file(filled().backup.create())
    change(backup)
    target = filled()
    before = target.everything()
    with pytest.raises(ValidationError, match=message):
        target.backup.restore(backup)
    assert target.everything() == before


def test_an_upload_missing_from_the_backup_leaves_the_card_without_image():
    backup = through_a_file(filled().backup.create())
    backup["images"] = {}
    target = Install()
    target.backup.restore(backup)
    wiki = next(link for link in target.links.list_all() if link.title == "Wiki")
    assert wiki.image_url == ""


def test_command_cards_of_an_older_backup_are_left_out():
    backup = through_a_file(filled().backup.create())
    image_url = backup["links"][1]["image_url"]
    backup["links"].append(
        {
            **backup["links"][0],
            "id": "c" * 32,
            "title": "IGV",
            "url": "",
            "kind": "command",
            "command": "igv.sh",
        }
    )
    backup["links"].append({**backup["links"][1], "id": "d" * 32, "image_url": "/images/gone.png"})
    backup["links"][-1].update(kind="command", command="x", url="", title="Deploy")
    backup["images"]["/images/gone.png"] = backup["images"][image_url]

    target = Install()
    summary = target.backup.check(backup)
    assert (summary.links, summary.command_cards, summary.images) == (2, 2, 1)
    target.backup.restore(backup)
    assert [link.title for link in target.links.list_all()] == ["Console", "Wiki"]
    assert len(target.store.saved) == 1  # the command card's image is not saved
