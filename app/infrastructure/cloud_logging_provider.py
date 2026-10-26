"""LogProvider backed by the Cloud Logging API client.

Used where the gcloud CLI is unavailable (containers): authenticates with
Application Default Credentials (the file at GOOGLE_APPLICATION_CREDENTIALS), which need roles/logging.viewer on every
project queried. `describe`
still returns the equivalent gcloud command so the UI copy button keeps
working on any machine.
"""

import re
from datetime import datetime, timedelta, timezone
from itertools import islice

from app.domain.logs import LogEntry, LogProvider, LogQuery
from app.domain.errors import LogProviderError
from app.infrastructure.log_entry_parsing import describe_gcloud_command, parse_entry

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
    seconds = sum(
        int(amount) * _UNIT_SECONDS[unit.lower()]
        for amount, unit in _DURATION_TOKEN.findall(text)
    )
    return timedelta(seconds=seconds)


class CloudLoggingApiProvider(LogProvider):
    def __init__(self):
        self._client = None

    def describe(self, query: LogQuery) -> str:
        return describe_gcloud_command(query)

    def query(self, query: LogQuery) -> list[LogEntry]:
        from google.api_core import exceptions as api_exceptions
        from google.auth import exceptions as auth_exceptions
        from google.cloud import logging as cloud_logging

        if self._client is None:
            try:
                self._client = cloud_logging.Client()
            except auth_exceptions.DefaultCredentialsError as error:
                raise LogProviderError(
                    f"No Google Cloud credentials (ADC): {error}"
                ) from None

        filter_ = self._build_filter(query)
        try:
            iterator = self._client.list_entries(
                resource_names=[f"projects/{query.project}"],
                filter_=filter_,
                order_by=cloud_logging.DESCENDING,
                page_size=min(query.limit, 1000),
            )
            return [parse_entry(entry.to_api_repr()) for entry in islice(iterator, query.limit)]
        except api_exceptions.PermissionDenied:
            raise LogProviderError(
                f"No permission to read logs of project '{query.project}'."
                " Grant roles/logging.viewer to the credentials in use."
            ) from None
        except api_exceptions.GoogleAPICallError as error:
            raise LogProviderError(
                f"Cloud Logging API falhou: {error.message or error}"
            ) from None

    @staticmethod
    def _build_filter(query: LogQuery) -> str:
        expression = query.expression.strip()
        # gcloud only applies --freshness when the filter has no timestamp
        # clause of its own; mirror that so saved filters behave the same.
        if "timestamp" in expression:
            return expression
        cutoff = datetime.now(timezone.utc) - _freshness_to_timedelta(query.freshness)
        clause = f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%S")}Z"'
        if not expression:
            return clause
        return f"({expression}) AND {clause}"
