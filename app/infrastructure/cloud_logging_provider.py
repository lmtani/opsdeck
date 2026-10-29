"""LogProvider for GCP log sources, backed by the Cloud Logging API client.

Authenticates with Application Default Credentials (the file at
GOOGLE_APPLICATION_CREDENTIALS, else the well-known location), which need
roles/logging.viewer on every project queried; no gcloud CLI is run. The
client is created per queried project, so no GOOGLE_CLOUD_PROJECT is needed: user
credentials from `gcloud auth application-default login` carry none.
Credentials are refreshed before the first query, so expired or revoked ones
fail at once with instructions instead of after a minute of gRPC retries.
`describe` returns the equivalent gcloud command for the UI copy button and
`console_url` the query in the Logs Explorer. Refinements become Cloud
Logging clauses here (`expression`), so the frontend never writes the syntax.
"""

import re
import shlex
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from urllib.parse import quote

from app.domain.errors import LogProviderError
from app.domain.logs import Freshness, LogEntry, LogProvider, LogQuery, LogSource, Refinement, RefinementValue
from app.infrastructure.log_entry_parsing import parse_entry

SCOPES = ("https://www.googleapis.com/auth/logging.read",)
LOGIN_HINT = (
    "Run `gcloud auth application-default login`; with Docker, copy the new"
    " ~/.config/gcloud/application_default_credentials.json to secrets/gcp-adc.json"
    " (see the Logs tab section of the README)."
)

# gcloud duration syntax, e.g. '30m', '1h', '2d', '1d12h'; a bare number
# means days (mirrors `gcloud logging read --freshness`).
_DURATION_TOKEN = re.compile(r"(\d+)([smhdw])", re.IGNORECASE)
_DURATION_FULL = re.compile(r"^(?:\d+[smhdw])+$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# a path segment that needs no quotes in the filter language
_PLAIN_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# freshness -> ISO-8601 duration of the Logs Explorer's timeRange parameter
_TIME_RANGE = {
    Freshness.M15: "PT15M",
    Freshness.H1: "PT1H",
    Freshness.H6: "PT6H",
    Freshness.D1: "P1D",
    Freshness.D3: "P3D",
    Freshness.D7: "P7D",
}


def _quote(text: str) -> str:
    """A filter-language string literal: quotes and backslashes escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _field(path: tuple[str, ...]) -> str:
    """labels."k8s-pod/app": segments that are not identifiers are quoted."""
    return ".".join(part if _PLAIN_SEGMENT.match(part) else _quote(part) for part in path)


def _literal(value: RefinementValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _quote(value)


def refinement_clause(refinement: Refinement) -> str:
    operator = "!=" if refinement.negate else "="
    return f"{_field(refinement.path)}{operator}{_literal(refinement.value)}"


def refined_expression(query: LogQuery) -> str:
    """The saved expression, parenthesized, ANDed with each refinement."""
    clauses = " AND ".join(refinement_clause(item) for item in query.refinements)
    if not clauses:
        return query.expression
    return f"({query.expression}) AND {clauses}" if query.expression else clauses


def gcloud_args(project: str, query: LogQuery) -> list[str]:
    args = ["gcloud", "logging", "read"]
    expression = refined_expression(query)
    if expression:
        args.append(expression)
    args += [
        f"--project={project}",
        f"--limit={query.limit}",
        f"--freshness={query.freshness}",
        "--order=desc",
    ]
    return args


def logs_explorer_url(project: str, query: LogQuery) -> str:
    return (
        "https://console.cloud.google.com/logs/query"
        f";query={quote(refined_expression(query), safe='')}"
        f";timeRange={_TIME_RANGE[query.freshness]}"
        f"?project={quote(project, safe='')}"
    )


def _freshness_to_timedelta(freshness: str) -> timedelta:
    text = (freshness or "").strip()
    if text.isdigit():
        return timedelta(days=int(text))
    if not _DURATION_FULL.match(text):
        raise LogProviderError(f"Invalid freshness: '{freshness}' (e.g. 30m, 1h, 2d)")
    seconds = sum(int(amount) * _UNIT_SECONDS[unit.lower()] for amount, unit in _DURATION_TOKEN.findall(text))
    return timedelta(seconds=seconds)


def _well_known_adc() -> Path:
    """Where `gcloud auth application-default login` writes, as google-auth sees it
    (it honours CLOUDSDK_CONFIG)."""
    from google.auth import _cloud_sdk

    return Path(_cloud_sdk.get_application_default_credentials_path())


def _default_credentials():
    """Application Default Credentials, refreshed once so problems show now."""
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=SCOPES)
    credentials.refresh(Request())
    return credentials


def _default_client(project: str, credentials):
    from google.cloud import logging as cloud_logging

    return cloud_logging.Client(project=project, credentials=credentials)


class CloudLoggingApiProvider(LogProvider):
    def __init__(
        self,
        credentials_file: str = "",
        load_credentials: Callable[[], object] = _default_credentials,
        make_client: Callable[[str, object], object] = _default_client,
    ):
        self._credentials_file = credentials_file
        self._load_credentials = load_credentials
        self._make_client = make_client
        self._credentials = None
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Offline check: a readable credentials file is in place."""
        path = Path(self._credentials_file) if self._credentials_file else _well_known_adc()
        try:
            with path.open("rb"):
                return True
        except OSError:
            return False

    def expression(self, query: LogQuery) -> str:
        return refined_expression(query)

    def describe(self, source: LogSource, query: LogQuery) -> str:
        return shlex.join(gcloud_args(source.config["project"], query))

    def console_url(self, source: LogSource, query: LogQuery) -> str:
        return logs_explorer_url(source.config["project"], query)

    def query(self, source: LogSource, query: LogQuery) -> list[LogEntry]:
        from google.api_core import exceptions as api_exceptions

        project = source.config["project"]
        client = self._client(project)
        filter_ = self._build_filter(query)
        try:
            iterator = client.list_entries(
                resource_names=[f"projects/{project}"],
                filter_=filter_,
                order_by="timestamp desc",
                page_size=min(query.limit, 1000),
            )
            return [parse_entry(entry.to_api_repr()) for entry in islice(iterator, query.limit)]
        except api_exceptions.PermissionDenied as error:
            raise LogProviderError(
                f"No permission to read the logs of project '{project}'. Grant"
                " roles/logging.viewer on it to the account behind the credentials, and"
                f" check that the Cloud Logging API is enabled there. ({_first_line(error)})"
            ) from None
        except api_exceptions.NotFound:
            raise LogProviderError(
                f"Project '{project}' was not found, or the credentials cannot see it."
            ) from None
        except api_exceptions.InvalidArgument as error:
            raise LogProviderError(
                f"Cloud Logging rejected the query: {_first_line(error)}. Check the filter expression."
            ) from None
        except api_exceptions.Unauthenticated:
            self._forget_credentials()
            raise LogProviderError(f"Google rejected the credentials. {LOGIN_HINT}") from None
        except (
            api_exceptions.RetryError,
            api_exceptions.DeadlineExceeded,
            api_exceptions.ServiceUnavailable,
        ) as error:
            raise LogProviderError(
                "Cloud Logging did not answer in time; check the network and try again."
                f" ({_first_line(error)})"
            ) from None
        except api_exceptions.GoogleAPICallError as error:
            raise LogProviderError(f"Cloud Logging API failed: {_first_line(error)}") from None

    # ----- credentials and clients -------------------------------------

    def _client(self, project: str):
        credentials = self._credentials_or_raise()
        with self._lock:
            if project not in self._clients:
                try:
                    self._clients[project] = self._make_client(project, credentials)
                except Exception as error:  # library raises OSError/ValueError on bad input
                    raise LogProviderError(
                        f"Could not create the Cloud Logging client for '{project}': {_first_line(error)}"
                    ) from None
            return self._clients[project]

    def _credentials_or_raise(self):
        from google.auth import exceptions as auth_exceptions

        with self._lock:
            if self._credentials is not None:
                return self._credentials
            try:
                self._credentials = self._load_credentials()
            except auth_exceptions.DefaultCredentialsError as error:
                raise LogProviderError(_missing_credentials_message(error)) from None
            except auth_exceptions.RefreshError as error:
                raise LogProviderError(_refresh_message(error)) from None
            except auth_exceptions.TransportError as error:
                raise LogProviderError(
                    "Could not reach Google to validate the credentials; check the network."
                    f" ({_first_line(error)})"
                ) from None
            except PermissionError:
                raise LogProviderError(
                    "The credentials file exists but cannot be read by the app. With Docker,"
                    " run `chmod 644 secrets/gcp-adc.json`."
                ) from None
            return self._credentials

    def _forget_credentials(self) -> None:
        with self._lock:
            self._credentials = None
            self._clients.clear()

    @staticmethod
    def _build_filter(query: LogQuery) -> str:
        expression = refined_expression(query)
        # gcloud only applies --freshness when the filter has no timestamp
        # clause of its own; mirror that so saved filters behave the same.
        if "timestamp" in expression:
            return expression
        cutoff = datetime.now(UTC) - _freshness_to_timedelta(query.freshness)
        clause = f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%S")}Z"'
        if not expression:
            return clause
        return f"({expression}) AND {clause}"


def _first_line(error: Exception) -> str:
    text = getattr(error, "message", None) or str(error)
    return text.strip().splitlines()[0] if text.strip() else type(error).__name__


def _missing_credentials_message(error: Exception) -> str:
    text = str(error)
    if "not found" in text.lower() or "could not automatically determine" in text.lower():
        return f"No Google Cloud credentials found. {LOGIN_HINT}"
    return f"The Google Cloud credentials file is not usable: {_first_line(error)}. {LOGIN_HINT}"


def _refresh_message(error: Exception) -> str:
    text = str(error).lower()
    if "invalid_grant" in text or "reauth" in text:
        reason = "The Google Cloud credentials expired or were revoked."
    elif "invalid_client" in text or "unauthorized_client" in text:
        reason = "Google does not recognize these credentials (invalid client)."
    else:
        reason = f"Google rejected the credentials ({_first_line(error)})."
    return f"{reason} {LOGIN_HINT}"
