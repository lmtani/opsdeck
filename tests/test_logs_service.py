"""Logs context use cases: sources, saved filters and queries. In-memory fakes only."""

import pytest

from app.application.logs import LogFilterService, LogQueryService, LogSourceService
from app.domain.errors import LogProviderError, NotFoundError, ValidationError
from app.domain.logs import LogFilter, LogQuery, LogSourceKind, Refinement
from tests.fakes import FakeLogProvider, InMemoryFilterRepository, InMemorySourceRepository, gcp_source


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
