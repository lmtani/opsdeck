"""Logs context use cases: saved filters and queries. In-memory fakes only."""

import pytest

from app.application.logs import LogFilterService, LogQueryService
from app.domain.errors import LogProviderError, NotFoundError, ValidationError
from app.domain.logs import LogFilter, LogQuery
from tests.fakes import FakeLogProvider, InMemoryFilterRepository


def test_run_saved_filter_builds_query_from_filter():
    saved = LogFilter(
        id="f1",
        name="erros",
        project="proj-x",
        expression="severity>=ERROR",
        freshness="6h",
        limit=25,
    )
    provider = FakeLogProvider()
    service = LogQueryService(provider, InMemoryFilterRepository([saved]))

    command, entries = service.run_saved_filter("f1")

    assert command == "fake --project=proj-x"
    assert len(entries) == 1
    assert provider.last_query.project == "proj-x"
    assert provider.last_query.expression == "severity>=ERROR"
    assert provider.last_query.freshness == "6h"
    assert provider.last_query.limit == 25

def test_run_saved_filter_unknown_id():
    service = LogQueryService(FakeLogProvider(), InMemoryFilterRepository())
    with pytest.raises(NotFoundError):
        service.run_saved_filter("ghost")

def test_a_query_needs_a_project():
    with pytest.raises(ValidationError):
        LogQuery(project="  ", expression="")


# ----- saved filters ---------------------------------------------------------


def test_create_filter_trims_and_persists():
    repository = InMemoryFilterRepository()
    service = LogFilterService(repository)
    created = service.create_filter(
        name=" Erros ", project=" proj-x ", expression=' severity>="ERROR" ',
        freshness="6h", limit=25, description=" desc ", line_format=" {status} ",
    )
    assert created.id and created.created_at
    assert (created.name, created.project) == ("Erros", "proj-x")
    assert created.expression == 'severity>="ERROR"'
    assert (created.freshness, created.limit) == ("6h", 25)
    assert (created.description, created.line_format) == ("desc", "{status}")
    assert repository.get(created.id) == created


@pytest.mark.parametrize("kwargs", [dict(name="  ", project="p"), dict(name="n", project=" ")])
def test_create_filter_requires_name_and_project(kwargs):
    with pytest.raises(ValidationError):
        LogFilterService(InMemoryFilterRepository()).create_filter(expression="", **kwargs)


def test_update_filter_replaces_fields_and_keeps_identity():
    service = LogFilterService(InMemoryFilterRepository())
    created = service.create_filter(name="a", project="p", expression="e")
    updated = service.update_filter(
        created.id, name="b", project="q", expression="f", freshness="1d", limit=10,
        description="d", line_format="{x}",
    )
    assert updated.id == created.id and updated.created_at == created.created_at
    assert (updated.name, updated.project, updated.expression) == ("b", "q", "f")
    assert (updated.freshness, updated.limit, updated.line_format) == ("1d", 10, "{x}")
    assert [item.id for item in service.list_filters()] == [created.id]


def test_update_or_delete_missing_filter_raises_not_found():
    service = LogFilterService(InMemoryFilterRepository())
    with pytest.raises(NotFoundError):
        service.update_filter("ghost", name="a", project="p", expression="")
    with pytest.raises(NotFoundError):
        service.delete_filter("ghost")


def test_delete_and_reorder_filters():
    service = LogFilterService(InMemoryFilterRepository())
    first = service.create_filter(name="a", project="p", expression="")
    second = service.create_filter(name="b", project="p", expression="")
    third = service.create_filter(name="c", project="p", expression="")

    service.reorder_filters([third.id, "ghost", first.id, second.id])
    assert [item.id for item in service.list_filters()] == [third.id, first.id, second.id]

    service.delete_filter(first.id)
    assert [item.id for item in service.list_filters()] == [third.id, second.id]


def test_run_query_reports_provider_failures():
    provider = FakeLogProvider(error=LogProviderError("gcloud fora"))
    service = LogQueryService(provider, InMemoryFilterRepository())
    with pytest.raises(LogProviderError, match="gcloud fora"):
        service.run_query(LogQuery(project="p", expression=""))
    assert service.provider_available() is True
    assert LogQueryService(FakeLogProvider(available=False), InMemoryFilterRepository()).provider_available() is False
