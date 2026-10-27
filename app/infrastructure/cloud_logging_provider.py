"""LogProvider backed by the Cloud Logging API client.

Used where the gcloud CLI is unavailable (containers): authenticates with
Application Default Credentials (the file at GOOGLE_APPLICATION_CREDENTIALS),
which need roles/logging.viewer on every project queried. The client is
created per queried project, so no GOOGLE_CLOUD_PROJECT is needed: user
credentials from `gcloud auth application-default login` carry none.
Credentials are refreshed before the first query, so expired or revoked ones
fail at once with instructions instead of after a minute of gRPC retries.
`describe` still returns the equivalent gcloud command so the UI copy button
keeps working on any machine.
"""

import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path

from app.domain.errors import LogProviderError
from app.domain.logs import LogEntry, LogProvider, LogQuery
from app.infrastructure.log_entry_parsing import describe_gcloud_command, parse_entry

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

    def describe(self, query: LogQuery) -> str:
        return describe_gcloud_command(query)

    def query(self, query: LogQuery) -> list[LogEntry]:
        from google.api_core import exceptions as api_exceptions

        client = self._client(query.project)
        filter_ = self._build_filter(query)
        try:
            iterator = client.list_entries(
                resource_names=[f"projects/{query.project}"],
                filter_=filter_,
                order_by="timestamp desc",
                page_size=min(query.limit, 1000),
            )
            return [parse_entry(entry.to_api_repr()) for entry in islice(iterator, query.limit)]
        except api_exceptions.PermissionDenied as error:
            raise LogProviderError(
                f"No permission to read the logs of project '{query.project}'. Grant"
                " roles/logging.viewer on it to the account behind the credentials, and"
                f" check that the Cloud Logging API is enabled there. ({_first_line(error)})"
            ) from None
        except api_exceptions.NotFound:
            raise LogProviderError(
                f"Project '{query.project}' was not found, or the credentials cannot see it."
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
        expression = query.expression.strip()
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
