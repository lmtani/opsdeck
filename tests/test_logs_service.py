"""Logs context use cases: sources, saved filters and queries. In-memory fakes only."""

from datetime import timedelta

import pytest

from opsdeck.application.logs import LogFilterService, LogQueryService, LogSourceService, LogWatchService
from opsdeck.domain.errors import LogProviderError, NotFoundError, ValidationError
from opsdeck.domain.logs import (
    LogCount,
    LogEntry,
    LogFilter,
    LogQuery,
    LogSourceKind,
    Refinement,
    SeverityBand,
    TimeRange,
)
from tests.fakes import FakeLogProvider, InMemoryFilterRepository, InMemorySourceRepository, gcp_source, ts


def query_service(provider=None, sources=(), filters=()):
    return LogQueryService(
        {LogSourceKind.GCP: provider or FakeLogProvider()},
        InMemorySourceRepository(list(sources)),
        InMemoryFilterRepository(list(filters)),
    )


def test_run_saved_filter_reads_its_source():
    source = gcp_source("proj-x", id_="s1")
    saved = LogFilter(
        id="f1", name="erros", source_id="s1", expression="severity>=ERROR", freshness="6h", limit=25
    )
    provider = FakeLogProvider()
    result = query_service(provider, [source], [saved]).run_saved_filter("f1")

    assert result.command == "fake --project=proj-x"
    assert result.console_url == "https://console.example/proj-x"
    assert result.source is source and len(result.entries) == 1
    assert provider.last_source is source
    assert provider.last_query == LogQuery(expression="severity>=ERROR", freshness="6h", limit=25)


def test_run_saved_filter_with_refinements_reports_the_refined_expression():
    source = gcp_source(id_="s1")
    saved = LogFilter(id="f1", name="erros", source_id="s1", expression="severity>=ERROR")
    provider = FakeLogProvider()
    refinement = Refinement(path=("resource", "type"), value="gae_app", negate=True)

    result = query_service(provider, [source], [saved]).run_saved_filter("f1", [refinement])

    assert provider.last_query.refinements == (refinement,)
    assert result.expression == "severity>=ERROR AND resource.type!=gae_app"


def test_run_saved_filter_over_a_time_range_and_bands():
    """What a watched filter counted, even when it is older than the window."""
    saved = LogFilter(id="f1", name="erros", source_id="s1", expression="e", freshness="1h", limit=25)
    provider = FakeLogProvider()
    span = TimeRange(ts("2026-10-29T14:00:00Z"), ts("2026-10-31T10:00:00Z"))

    query_service(provider, [gcp_source(id_="s1")], [saved]).run_saved_filter(
        "f1", time_range=span, bands=[SeverityBand.WARNING, SeverityBand.ERROR]
    )
    assert provider.last_query.time_range == span and provider.last_query.limit == 25
    assert provider.last_query.bands == (SeverityBand.ERROR, SeverityBand.WARNING)


def test_run_saved_filter_or_query_on_unknown_ids():
    service = query_service(sources=[gcp_source(id_="s1")])
    with pytest.raises(NotFoundError):
        service.run_saved_filter("ghost")
    with pytest.raises(NotFoundError):
        service.run_query("ghost", LogQuery())


def test_run_query_reports_provider_failures():
    provider = FakeLogProvider(error=LogProviderError("gcloud fora"))
    service = query_service(provider, [gcp_source(id_="s1")])
    with pytest.raises(LogProviderError, match="gcloud fora"):
        service.run_query("s1", LogQuery(expression=""))


def test_availability_covers_the_kinds_in_use():
    assert query_service().providers_available() is None  # no sources: nothing to check
    assert query_service(sources=[gcp_source()]).providers_available() is True
    down = FakeLogProvider(available=False)
    assert query_service(down, [gcp_source()]).providers_available() is False


def test_a_kind_without_a_provider_is_reported():
    service = LogQueryService(
        {}, InMemorySourceRepository([gcp_source(id_="s1")]), InMemoryFilterRepository()
    )
    with pytest.raises(ValidationError, match="not supported"):
        service.run_query("s1", LogQuery())


# ----- sources ---------------------------------------------------------------


def test_create_source_trims_and_lists_by_name():
    service = LogSourceService(InMemorySourceRepository(), InMemoryFilterRepository())
    created = service.create_source(" Prod ", "gcp", {"project": " proj-prod ", "zone": "x"})
    service.create_source("billing", "gcp", {"project": "proj-billing"})

    assert (created.name, created.kind, created.config) == (
        "Prod",
        LogSourceKind.GCP,
        {"project": "proj-prod"},
    )
    assert [source.name for source in service.list_sources()] == ["billing", "Prod"]


@pytest.mark.parametrize(
    ("name", "kind", "config", "message"),
    [
        (" ", "gcp", {"project": "p"}, "Name is required"),
        ("a", "gcp", {"project": " "}, "Project is required"),
        ("a", "gcp", {}, "Project is required"),
        ("a", "splunk", {"project": "p"}, "Unknown kind"),
    ],
)
def test_create_source_validates(name, kind, config, message):
    service = LogSourceService(InMemorySourceRepository(), InMemoryFilterRepository())
    with pytest.raises(ValidationError, match=message):
        service.create_source(name, kind, config)


def test_source_names_are_unique_ignoring_case():
    service = LogSourceService(InMemorySourceRepository(), InMemoryFilterRepository())
    first = service.create_source("Prod", "gcp", {"project": "a"})
    second = service.create_source("staging", "gcp", {"project": "b"})
    with pytest.raises(ValidationError, match="already a log source named"):
        service.create_source("prod", "gcp", {"project": "c"})
    with pytest.raises(ValidationError, match="already a log source named"):
        service.update_source(second.id, "PROD", {"project": "b"})
    assert service.update_source(first.id, "prod", {"project": "a2"}).config == {"project": "a2"}


def test_update_source_keeps_identity_and_kind():
    service = LogSourceService(InMemorySourceRepository(), InMemoryFilterRepository())
    created = service.create_source("a", "gcp", {"project": "p"})
    updated = service.update_source(created.id, "b", {"project": "q"})
    assert (updated.id, updated.kind, updated.created_at) == (created.id, created.kind, created.created_at)
    assert (updated.name, updated.config) == ("b", {"project": "q"})
    with pytest.raises(ValidationError):
        service.update_source(created.id, "b", {"project": ""})
    assert updated.config == {"project": "q"}  # an invalid update changes nothing
    with pytest.raises(NotFoundError):
        service.update_source("ghost", "b", {"project": "q"})


def test_a_source_in_use_cannot_be_deleted():
    sources = InMemorySourceRepository([gcp_source(id_="s1"), gcp_source("other", id_="s2")])
    filters = InMemoryFilterRepository([LogFilter(id="f1", name="a", source_id="s1")])
    service = LogSourceService(sources, filters)
    with pytest.raises(ValidationError, match="1 filter still use"):
        service.delete_source("s1")
    service.delete_source("s2")
    assert [source.id for source in service.list_sources()] == ["s1"]
    with pytest.raises(NotFoundError):
        service.delete_source("s2")


# ----- saved filters ---------------------------------------------------------


@pytest.fixture
def filters():
    sources = InMemorySourceRepository([gcp_source(id_="s1"), gcp_source("proj-y", id_="s2")])
    repository = InMemoryFilterRepository()
    return LogFilterService(repository, sources), repository


def test_create_filter_trims_and_persists(filters):
    service, repository = filters
    created = service.create_filter(
        name=" Erros ",
        source_id=" s1 ",
        expression=' severity>="ERROR" ',
        freshness="6h",
        limit=25,
        description=" desc ",
        line_format=" {status} ",
    )
    assert created.id and created.created_at
    assert (created.name, created.source_id) == ("Erros", "s1")
    assert created.expression == 'severity>="ERROR"'
    assert (created.freshness, created.limit) == ("6h", 25)
    assert (created.description, created.line_format) == ("desc", "{status}")
    assert repository.get(created.id) == created


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(name="  ", source_id="s1"), "Name is required"),
        (dict(name="n", source_id=" "), "log source is required"),
        (dict(name="n", source_id="ghost"), "does not exist"),
    ],
)
def test_create_filter_requires_name_and_an_existing_source(filters, kwargs, message):
    service, repository = filters
    with pytest.raises(ValidationError, match=message):
        service.create_filter(expression="", **kwargs)
    assert repository.list_all() == []


def test_update_filter_replaces_fields_and_keeps_identity(filters):
    service, repository = filters
    created = service.create_filter(name="a", source_id="s1", expression="e")
    updated = service.update_filter(
        created.id,
        name="b",
        source_id="s2",
        expression="f",
        freshness="1d",
        limit=10,
        description="d",
        line_format="{x}",
    )
    assert updated.id == created.id and updated.created_at == created.created_at
    assert (updated.name, updated.source_id, updated.expression) == ("b", "s2", "f")
    assert (updated.freshness, updated.limit, updated.line_format) == ("1d", 10, "{x}")
    assert [item.id for item in service.list_filters()] == [created.id]

    with pytest.raises(ValidationError, match="does not exist"):
        service.update_filter(created.id, name="c", source_id="ghost")
    assert (repository.get(created.id).name, repository.get(created.id).source_id) == ("b", "s2")


def test_update_or_delete_missing_filter_raises_not_found(filters):
    service, _ = filters
    with pytest.raises(NotFoundError):
        service.update_filter("ghost", name="a", source_id="s1", expression="")
    with pytest.raises(NotFoundError):
        service.delete_filter("ghost")


def test_delete_and_reorder_filters(filters):
    service, _ = filters
    first = service.create_filter(name="a", source_id="s1", expression="")
    second = service.create_filter(name="b", source_id="s1", expression="")
    third = service.create_filter(name="c", source_id="s1", expression="")

    service.reorder_filters([third.id, "ghost", first.id, second.id])
    assert [item.id for item in service.list_filters()] == [third.id, first.id, second.id]

    service.delete_filter(first.id)
    assert [item.id for item in service.list_filters()] == [third.id, second.id]


# ----- watched filters ---------------------------------------------------------


class Clock:
    def __init__(self, at: str):
        self.now = ts(at)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def entry(at: str, severity: str = "ERROR") -> LogEntry:
    return LogEntry(timestamp=at, severity=severity, summary="x")


def watch_world(entries=(), **options):
    clock = Clock("2026-10-31T10:00:00Z")
    provider = FakeLogProvider(list(entries))
    filters = InMemoryFilterRepository([LogFilter(id="f1", name="errors", source_id="s1", expression="e")])
    service = LogWatchService(
        {LogSourceKind.GCP: provider},
        InMemorySourceRepository([gcp_source(id_="s1")]),
        filters,
        clock=clock,
        **options,
    )
    return service, provider, filters, clock


def test_a_watch_counts_only_what_arrives_after_it_is_turned_on():
    service, provider, filters, clock = watch_world(
        [
            entry("2026-10-31T09:59:00Z"),
            entry("2026-10-31T10:01:00Z"),
            entry("2026-10-31T10:02:30Z", "WARNING"),
        ]
    )
    assert service.unseen() == []  # nothing watched yet

    service.set_watch("f1", True)
    assert filters.get("f1").last_seen_at == ts("2026-10-31T10:00:00Z")
    [status] = service.unseen()
    assert status.count == LogCount() and provider.count_calls == []  # lag: nothing settled yet

    clock.advance(minutes=4)  # counts [10:00, 10:03): the 09:59 entry is older than the watch
    [status] = service.unseen()
    assert (status.count.error, status.count.warning) == (1, 1)
    assert (status.since, status.until) == (ts("2026-10-31T10:00:00Z"), ts("2026-10-31T10:03:00Z"))


def test_each_round_reads_only_the_new_interval_and_rounds_are_throttled():
    service, provider, _, clock = watch_world([entry("2026-10-31T10:01:00Z"), entry("2026-10-31T10:05:00Z")])
    service.set_watch("f1", True)
    clock.advance(minutes=3)
    assert service.unseen()[0].count.error == 1
    clock.advance(seconds=30)
    service.unseen()  # asked again within min_interval: no read
    assert len(provider.count_calls) == 1

    clock.advance(minutes=4)
    assert service.unseen()[0].count.error == 2
    (*_, end1), (*_, start2, end2) = provider.count_calls
    assert end1 == start2 and end2 == ts("2026-10-31T10:06:30Z")  # back to back, never overlapping


def test_opening_a_filter_restarts_its_count_and_unwatching_drops_it():
    service, _, _, clock = watch_world([entry("2026-10-31T10:01:00Z"), entry("2026-10-31T10:08:00Z")])
    service.set_watch("f1", True)
    clock.advance(minutes=3)
    assert service.unseen()[0].count.total == 1

    clock.advance(minutes=2)
    assert service.mark_seen("f1").last_seen_at == ts("2026-10-31T10:05:00Z")
    clock.advance(minutes=5)
    [status] = service.unseen()
    assert status.count.total == 1 and status.since == ts("2026-10-31T10:05:00Z")

    service.set_watch("f1", False)
    assert service.unseen() == []


def test_seen_up_to_an_instant_keeps_counting_what_came_after_it():
    """The new-entries view marks seen up to the end of what it showed."""
    service, _, _, clock = watch_world([entry("2026-10-31T10:01:00Z"), entry("2026-10-31T10:03:30Z")])
    service.set_watch("f1", True)
    clock.advance(minutes=5)
    [status] = service.unseen()
    assert status.count.total == 2 and status.until == ts("2026-10-31T10:04:00Z")

    assert service.mark_seen("f1", ts("2026-10-31T10:02:00Z")).last_seen_at == ts("2026-10-31T10:02:00Z")
    clock.advance(minutes=1)
    [status] = service.unseen()
    assert status.count.total == 1 and status.since == ts("2026-10-31T10:02:00Z")
    # never later than now, and never backwards
    assert service.mark_seen("f1", ts("2099-01-01T00:00:00Z")).last_seen_at == clock.now
    assert service.mark_seen("f1", ts("2026-10-31T10:00:00Z")).last_seen_at == clock.now


def test_a_failed_round_keeps_the_numbers_and_says_why():
    service, provider, _, clock = watch_world([entry("2026-10-31T10:01:00Z")])
    service.set_watch("f1", True)
    clock.advance(minutes=3)
    service.unseen()
    provider.error = LogProviderError("quota exceeded")
    clock.advance(minutes=2)
    [status] = service.unseen()
    assert status.problem == "quota exceeded" and status.count.total == 1
    assert status.until == ts("2026-10-31T10:02:00Z")  # the failed interval is read again later

    provider.error = None
    clock.advance(minutes=2)
    [status] = service.unseen()
    assert status.problem == "" and status.until == ts("2026-10-31T10:06:00Z")


def test_a_round_that_reaches_the_cap_is_a_lower_bound():
    times = [f"2026-10-31T10:01:{second:02d}Z" for second in range(5)]
    service, _, _, clock = watch_world([entry(at) for at in times], cap=3)
    service.set_watch("f1", True)
    clock.advance(minutes=3)
    [status] = service.unseen()
    assert status.count.total == 3 and status.count.capped


def test_watch_and_seen_on_unknown_filters():
    service, *_ = watch_world()
    with pytest.raises(NotFoundError):
        service.set_watch("ghost", True)
    with pytest.raises(NotFoundError):
        service.mark_seen("ghost")
