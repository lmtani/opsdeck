"""The update notice: versions, the cached check and the GitHub release feed."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from opsdeck.application.updates import CHECK_EVERY, RETRY_AFTER, UpdateService
from opsdeck.domain.errors import ExternalServiceError
from opsdeck.domain.updates import Release, parse_version
from opsdeck.infrastructure.github.releases import GithubReleaseFeed
from tests.fakes import FakeReleaseFeed

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_versions_compare_as_numbers():
    assert parse_version("v0.10.0") == (0, 10, 0) > parse_version("0.9.9")  # type: ignore[operator]
    assert parse_version("1.2") is None and parse_version("1.2.3rc1") is None
    assert parse_version("1.2.3+g1234") is None
    release = Release("0.10.0", "https://github.com/x/y/releases/tag/v0.10.0")
    assert release.is_newer_than("0.9.1")
    assert not release.is_newer_than("0.10.0") and not release.is_newer_than("0.11.0")
    assert not release.is_newer_than("0.1.dev3")  # an unparsable current version is never "behind"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def test_the_feed_is_read_once_per_interval():
    feed = FakeReleaseFeed(Release("0.3.0", "https://github.com/x/y/releases/tag/v0.3.0"))
    clock = Clock()
    service = UpdateService(feed, current="0.2.0", clock=clock)
    status = service.status()
    assert (status.current, status.latest and status.latest.version, status.newer) == ("0.2.0", "0.3.0", True)
    service.status()
    assert feed.reads == 1
    clock.now += CHECK_EVERY
    feed.release = Release("0.4.0", "https://github.com/x/y/releases/tag/v0.4.0")
    assert service.status().latest == feed.release and feed.reads == 2


def test_a_failed_read_keeps_the_last_answer_and_retries_sooner():
    feed = FakeReleaseFeed(Release("0.3.0", "https://github.com/x/y/releases/tag/v0.3.0"))
    clock = Clock()
    service = UpdateService(feed, current="0.2.0", clock=clock)
    service.status()
    clock.now += CHECK_EVERY
    feed.error = ExternalServiceError("down")
    assert service.status().newer is True  # still the 0.3.0 read before
    clock.now += RETRY_AFTER - timedelta(seconds=1)
    service.status()
    assert feed.reads == 2
    clock.now += timedelta(seconds=1)
    feed.error = None
    service.status()
    assert feed.reads == 3


def test_turned_off_it_never_reads():
    feed = FakeReleaseFeed(Release("9.0.0", "https://github.com/x/y/releases/tag/v9.0.0"))
    status = UpdateService(feed, current="0.2.0", enabled=False).status()
    assert (status.latest, status.newer, feed.reads) == (None, False, 0)


def feed_answering(status: int, body: object = None) -> GithubReleaseFeed:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/lmtani/opsdeck/releases/latest"
        assert "authorization" not in request.headers  # anonymous
        return httpx.Response(status, json=body)

    return GithubReleaseFeed("lmtani/opsdeck", transport=httpx.MockTransport(handler))


def test_github_feed_reads_the_latest_release():
    release = feed_answering(
        200,
        {
            "tag_name": "v0.3.0",
            "html_url": "https://github.com/lmtani/opsdeck/releases/tag/v0.3.0",
            "published_at": "2026-11-01T10:00:00Z",
        },
    ).latest()
    assert release == Release(
        "0.3.0",
        "https://github.com/lmtani/opsdeck/releases/tag/v0.3.0",
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
    )


def test_github_feed_without_releases_or_with_odd_ones():
    assert feed_answering(404, {"message": "Not Found"}).latest() is None
    assert feed_answering(200, {"tag_name": "nightly", "html_url": "https://github.com/x"}).latest() is None
    odd = feed_answering(200, {"tag_name": "v1.0.0", "html_url": "javascript:alert(1)"}).latest()
    assert odd is not None and odd.url == "https://github.com/lmtani/opsdeck/releases"


@pytest.mark.parametrize(
    ("status", "body"), [(403, {"message": "rate limited"}), (200, ["not", "a", "dict"])]
)
def test_github_feed_failures_are_external_errors(status, body):
    with pytest.raises(ExternalServiceError):
        feed_answering(status, body).latest()


def test_a_service_install_is_told_to_restart_the_service():
    feed = FakeReleaseFeed(Release("0.3.0", "https://github.com/x/y/releases/tag/v0.3.0"))
    assert UpdateService(feed, current="0.2.0", service=True).status().service is True
    assert UpdateService(feed, current="0.2.0").status().service is False
