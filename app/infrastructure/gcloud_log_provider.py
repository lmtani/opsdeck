"""LogProvider implementation backed by the gcloud CLI.

Uses the user's existing `gcloud auth` session, so no service-account keys
or extra GCP SDK dependencies are needed for a local, single-user setup.
"""

import json
import shutil
import subprocess

from app.domain.logs import LogEntry, LogProvider, LogQuery
from app.domain.errors import LogProviderError
from app.infrastructure.log_entry_parsing import (
    build_gcloud_args,
    describe_gcloud_command,
    parse_entry,
)


class GcloudLogProvider(LogProvider):
    def __init__(self, binary: str = "gcloud", timeout_seconds: int = 60):
        self._binary = binary
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def describe(self, query: LogQuery) -> str:
        return describe_gcloud_command(query, self._binary)

    def query(self, query: LogQuery) -> list[LogEntry]:
        if not self.is_available():
            raise LogProviderError(
                "gcloud not found in PATH. Install the Google Cloud SDK."
            )
        args = build_gcloud_args(query, self._binary) + ["--format=json"]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=self._timeout
            )
        except subprocess.TimeoutExpired:
            raise LogProviderError(
                f"gcloud timed out after {self._timeout}s"
            ) from None
        if result.returncode != 0:
            raise LogProviderError(_tail(result.stderr) or "gcloud failed without a message")
        try:
            raw_entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as error:
            raise LogProviderError(f"Invalid gcloud output: {error}") from None
        return [self._parse_entry(item) for item in raw_entries]

    # kept as a method for backwards compatibility (tests exercise it here)
    @staticmethod
    def _parse_entry(item: dict) -> LogEntry:
        return parse_entry(item)


def _tail(text: str, lines: int = 6) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    return "\n".join(stripped.splitlines()[-lines:])
