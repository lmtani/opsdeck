"""Logs context: saved Cloud Logging queries and the entries they return."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, utc_now
from app.domain.errors import ValidationError
from app.domain.repository import Repository

MAX_LIMIT = 500


class Freshness(StrEnum):
    """How far back a query looks, in gcloud duration syntax."""

    M15 = "15m"
    H1 = "1h"
    H6 = "6h"
    D1 = "1d"
    D3 = "3d"
    D7 = "7d"


def _freshness(value) -> Freshness:
    try:
        return Freshness(value)
    except ValueError:
        raise ValidationError("Invalid time window") from None


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValidationError(f"Limit must be between 1 and {MAX_LIMIT}")


def _clean(text: str | None) -> str:
    return (text or "").strip()


@dataclass
class LogQuery:
    """Parameters for a single log read, saved or ad-hoc. Needs a project."""

    project: str
    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = 50

    def __post_init__(self):
        self.project = _clean(self.project)
        self.expression = _clean(self.expression)
        self.freshness = _freshness(self.freshness)
        if not self.project:
            raise ValidationError("GCP project is required")
        _check_limit(self.limit)


def _fields(name, project, expression, freshness, limit, description, line_format) -> dict:
    return dict(
        name=_clean(name),
        project=_clean(project),
        expression=_clean(expression),
        freshness=_freshness(freshness),
        limit=limit,
        description=_clean(description),
        line_format=_clean(line_format),
    )


@dataclass
class LogFilter:
    """A saved GCP Logging query the user can re-run with one click.

    Invariants: a name, a project and a limit within 1..MAX_LIMIT. Build
    with `new`, edit with `update`; both trim the text fields.
    """

    id: str
    name: str
    project: str
    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = 50
    description: str = ""
    line_format: str = ""
    position: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        self.freshness = _freshness(self.freshness)
        if not self.name.strip():
            raise ValidationError("Name is required")
        if not self.project.strip():
            raise ValidationError("GCP project is required")
        _check_limit(self.limit)

    @classmethod
    def new(
        cls,
        name: str,
        project: str,
        expression: str = "",
        freshness: Freshness | str = Freshness.H1,
        limit: int = 50,
        description: str = "",
        line_format: str = "",
        position: int = 0,
    ) -> "LogFilter":
        values = _fields(name, project, expression, freshness, limit, description, line_format)
        return cls(id=new_id(), position=position, **values)

    def update(
        self,
        name: str,
        project: str,
        expression: str = "",
        freshness: Freshness | str = Freshness.H1,
        limit: int = 50,
        description: str = "",
        line_format: str = "",
    ) -> None:
        """Replace the editable fields; id, position and created_at stay."""
        values = _fields(name, project, expression, freshness, limit, description, line_format)
        replace(self, **values)  # validates the result before anything changes
        for field_name, value in values.items():
            setattr(self, field_name, value)

    def query(self) -> LogQuery:
        return LogQuery(
            project=self.project,
            expression=self.expression,
            freshness=self.freshness,
            limit=self.limit,
        )


@dataclass
class LogEntry:
    """A normalized log entry, independent of the provider that produced it."""

    timestamp: str
    severity: str
    summary: str
    resource_type: str = ""
    resource_labels: dict = field(default_factory=dict)
    payload: dict | str | None = None
    log_name: str = ""
    insert_id: str = ""
    trace: str = ""  # "projects/<project>/traces/<id>" when the entry belongs to a request trace
    span_id: str = ""
    operation_id: str = ""  # operation.id, correlates long-running / batch work without a trace
    fields: dict = field(default_factory=dict)  # full raw provider record, for line formatting


class LogFilterRepository(Repository[LogFilter]):
    """Saved filters, by id."""


class LogProvider(ABC):
    """Reads log entries from some backend (gcloud CLI, API client, ...)."""

    def is_available(self) -> bool:
        """Whether the backend is ready to serve queries (health check)."""
        return True

    @abstractmethod
    def query(self, query: LogQuery) -> list[LogEntry]: ...

    @abstractmethod
    def describe(self, query: LogQuery) -> str:
        """Human-readable equivalent command, shown in the UI for copy/paste."""
