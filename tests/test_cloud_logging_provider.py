"""CloudLoggingApiProvider with injected credentials and clients — no network.

The failures are the real exception types raised by google-auth and
google-api-core, so each one is checked to become a LogProviderError that
says what to do, instead of an unhandled 500.
"""

import pytest
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions

from app.domain.errors import LogProviderError
from app.domain.logs import LogQuery
from app.infrastructure.cloud_logging_provider import CloudLoggingApiProvider

QUERY = LogQuery(project="proj-x", expression='severity>="ERROR"', limit=2)


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
    entries = provider.query(QUERY)
    assert [e.summary for e in entries] == ["a", "b"]  # limit applied
    assert made == [("proj-x", "creds")]
    assert client.calls[0]["resource_names"] == ["projects/proj-x"]
    provider.query(QUERY)
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
        provider.query(QUERY)
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
        provider.query(QUERY)


def test_client_creation_errors_are_reported():
    def make(project, credentials):
        raise OSError("Project was not passed and could not be determined from the environment.")

    provider = CloudLoggingApiProvider(load_credentials=lambda: "creds", make_client=make)
    with pytest.raises(LogProviderError, match="Could not create the Cloud Logging client"):
        provider.query(QUERY)


def test_availability_follows_the_credentials_file(tmp_path):
    missing = CloudLoggingApiProvider(credentials_file=str(tmp_path / "nope.json"))
    assert missing.is_available() is False
    present = tmp_path / "adc.json"
    present.write_text("{}")
    assert CloudLoggingApiProvider(credentials_file=str(present)).is_available() is True
