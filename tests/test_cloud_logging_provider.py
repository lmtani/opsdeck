"""CloudLoggingApiProvider with injected credentials and clients — no network.

The failures are the real exception types raised by google-auth and
google-api-core, so each one is checked to become a LogProviderError that
says what to do, instead of an unhandled 500.
"""

from datetime import UTC, datetime

import pytest
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions

from app.domain.errors import LogProviderError
from app.domain.logs import LogQuery, Refinement, SeverityBand, TimeRange
from app.infrastructure.logs.gcp.provider import CloudLoggingApiProvider
from tests.fakes import gcp_source

SOURCE = gcp_source("proj-x")
QUERY = LogQuery(expression='severity>="ERROR"', limit=2)


class _Entry:
    def __init__(self, payload):
        self._payload = payload

    def to_api_repr(self):
        return {"timestamp": "2026-10-26T10:00:00Z", "severity": "ERROR", "textPayload": self._payload}


class FakeClient:
    def __init__(self, entries=None, error=None):
        self.entries = entries or []
        self.error = error
        self.calls = []

    def list_entries(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.entries)


def _provider(client=None, credentials_error=None):
    made = []

    def load():
        if credentials_error:
            raise credentials_error
        return "creds"

    def make(project, credentials):
        made.append((project, credentials))
        return client or FakeClient()

    provider = CloudLoggingApiProvider(load_credentials=load, make_client=make)
    return provider, made


def test_the_client_gets_the_queried_project_so_no_env_project_is_needed():
    client = FakeClient([_Entry("a"), _Entry("b"), _Entry("c")])
    provider, made = _provider(client)
    entries = provider.query(SOURCE, QUERY)
    assert [e.summary for e in entries] == ["a", "b"]  # limit applied
    assert made == [("proj-x", "creds")]
    assert client.calls[0]["resource_names"] == ["projects/proj-x"]
    provider.query(SOURCE, QUERY)
    assert len(made) == 1  # one client per project, reused


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            auth_exceptions.DefaultCredentialsError("File /secrets/gcp-adc.json was not found."),
            "No Google Cloud credentials found",
        ),
        (
            auth_exceptions.DefaultCredentialsError("File x is not a valid json file."),
            "credentials file is not usable",
        ),
        (
            auth_exceptions.RefreshError("invalid_grant: Token has been expired or revoked."),
            "expired or were revoked",
        ),
        (
            auth_exceptions.RefreshError("invalid_client: The OAuth client was not found."),
            "does not recognize these credentials",
        ),
        (auth_exceptions.TransportError("connection refused"), "Could not reach Google"),
        (PermissionError(13, "Permission denied"), "chmod 644 secrets/gcp-adc.json"),
    ],
)
def test_credential_problems_fail_fast_with_instructions(error, expected):
    provider, made = _provider(credentials_error=error)
    with pytest.raises(LogProviderError, match=expected) as raised:
        provider.query(SOURCE, QUERY)
    assert made == []  # failed before any API call
    if not isinstance(error, (auth_exceptions.TransportError, PermissionError)):
        assert "gcloud auth application-default login" in str(raised.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            api_exceptions.PermissionDenied("Permission 'logging.logEntries.list' denied"),
            "roles/logging.viewer",
        ),
        (api_exceptions.NotFound("project"), "was not found"),
        (
            api_exceptions.InvalidArgument("Unparseable filter: syntax error at line 1"),
            "Check the filter expression",
        ),
        (api_exceptions.RetryError("Timeout of 60.0s exceeded", cause=None), "did not answer in time"),
        (api_exceptions.ServiceUnavailable("unavailable"), "did not answer in time"),
        (api_exceptions.Unauthenticated("bad token"), "gcloud auth application-default login"),
        (api_exceptions.InternalServerError("boom"), "Cloud Logging API failed: boom"),
    ],
)
def test_api_errors_become_readable(error, expected):
    provider, _ = _provider(FakeClient(error=error))
    with pytest.raises(LogProviderError, match=expected):
        provider.query(SOURCE, QUERY)


def test_client_creation_errors_are_reported():
    def make(project, credentials):
        raise OSError("Project was not passed and could not be determined from the environment.")

    provider = CloudLoggingApiProvider(load_credentials=lambda: "creds", make_client=make)
    with pytest.raises(LogProviderError, match="Could not create the Cloud Logging client"):
        provider.query(SOURCE, QUERY)


def test_availability_follows_the_credentials_file(tmp_path):
    missing = CloudLoggingApiProvider(credentials_file=str(tmp_path / "nope.json"))
    assert missing.is_available() is False
    present = tmp_path / "adc.json"
    present.write_text("{}")
    assert CloudLoggingApiProvider(credentials_file=str(present)).is_available() is True


# ----- the Cloud Logging query language -----------------------------------------


def test_refinements_become_clauses_with_quoted_paths_and_literals():
    provider = CloudLoggingApiProvider()
    query = LogQuery(
        expression="severity>=ERROR",
        refinements=(
            Refinement(path=("resource", "type"), value="gae_app"),
            Refinement(path=("labels", "k8s-pod/app"), value='say "hi" \\ bye', negate=True),
            Refinement(path=("httpRequest", "status"), value=500),
            Refinement(path=("jsonPayload", "ok"), value=False),
        ),
    )
    assert provider.expression(query) == (
        '(severity>=ERROR) AND resource.type="gae_app"'
        ' AND labels."k8s-pod/app"!="say \\"hi\\" \\\\ bye"'
        " AND httpRequest.status=500 AND jsonPayload.ok=false"
    )
    assert provider.expression(LogQuery(expression="x")) == "x"
    only_refined = LogQuery(refinements=(Refinement(path=("severity",), value="ERROR"),))
    assert provider.expression(only_refined) == 'severity="ERROR"'


def test_the_query_sent_carries_refinements_and_the_freshness_cutoff():
    client = FakeClient()
    provider, _ = _provider(client)
    provider.query(SOURCE, QUERY.refined([Refinement(path=("severity",), value="ERROR")]))
    sent = client.calls[0]["filter_"]
    assert sent.startswith('((severity>="ERROR") AND severity="ERROR") AND timestamp>="')


def test_describe_and_console_url_use_the_source_project_and_the_refined_expression():
    provider = CloudLoggingApiProvider()
    query = LogQuery(
        expression="severity>=ERROR",
        freshness="6h",
        limit=25,
        refinements=(Refinement(path=("resource", "type"), value="gae_app"),),
    )
    command = provider.describe(SOURCE, query)
    assert command.startswith("gcloud logging read ")
    assert "'(severity>=ERROR) AND resource.type=\"gae_app\"'" in command
    assert command.endswith("--project=proj-x --limit=25 --freshness=6h --order=desc")

    url = provider.console_url(SOURCE, query)
    assert url == (
        "https://console.cloud.google.com/logs/query"
        ";query=%28severity%3E%3DERROR%29%20AND%20resource.type%3D%22gae_app%22"
        ";timeRange=PT6H?project=proj-x"
    )


def test_count_reads_one_page_of_the_interval_and_folds_severities():
    class _Sev:
        def __init__(self, severity):
            self.severity = severity

        def to_api_repr(self):
            return {"severity": self.severity}

    client = FakeClient([_Sev("ERROR"), _Sev("WARNING"), _Sev("CRITICAL")])
    provider, _ = _provider(client)
    start, end = datetime(2026, 10, 31, 10, 0, tzinfo=UTC), datetime(2026, 10, 31, 10, 2, 30, tzinfo=UTC)

    count = provider.count(SOURCE, QUERY, start, end, cap=1000)
    assert (count.error, count.warning, count.total, count.capped) == (2, 1, 3, False)
    call = client.calls[0]
    assert call["page_size"] == 1000
    assert call["filter_"] == (
        '(severity>="ERROR") AND timestamp>="2026-10-31T10:00:00.000000Z"'
        ' AND timestamp<"2026-10-31T10:02:30.000000Z"'
    )
    assert provider.count(SOURCE, QUERY, start, end, cap=3).capped  # a full page is a lower bound


def test_a_time_range_replaces_the_freshness_and_bands_narrow_the_severity():
    client = FakeClient([])
    provider, _ = _provider(client)
    span = TimeRange(datetime(2026, 10, 29, 14, 2, tzinfo=UTC), datetime(2026, 10, 31, 10, 0, tzinfo=UTC))
    query = LogQuery(
        expression="resource.type=gae_app",
        freshness="1h",
        limit=100,
        time_range=span,
        bands=(SeverityBand.ERROR, SeverityBand.WARNING),
    )
    bands = "(severity>=ERROR OR severity=WARNING)"
    stamps = 'timestamp>="2026-10-29T14:02:00.000000Z" AND timestamp<"2026-10-31T10:00:00.000000Z"'

    provider.query(SOURCE, query)
    assert client.calls[0]["filter_"] == f"((resource.type=gae_app) AND {bands}) AND {stamps}"
    assert provider.expression(query) == f"(resource.type=gae_app) AND {bands}"  # what a save keeps

    command = provider.describe(SOURCE, query)
    assert "--freshness" not in command and stamps in command
    url = provider.console_url(SOURCE, query)
    assert ";timeRange=2026-10-29T14%3A02%3A00.000000Z%2F2026-10-31T10%3A00%3A00.000000Z?" in url

    info = provider.expression(LogQuery(bands=(SeverityBand.INFO,)))
    other = provider.expression(LogQuery(bands=(SeverityBand.OTHER,)))
    assert (info, other) == ("(severity=INFO OR severity=NOTICE)", "severity<=DEBUG")
