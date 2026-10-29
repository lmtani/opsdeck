"""Logs context: log sources, the saved queries that run against them and the
entries they return.

A source is one place logs are read from (today a Google Cloud project);
its `kind` picks the LogProvider that talks to it, and the filters saved on
it are written in that provider's query language.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, utc_now
from app.domain.errors import ValidationError
from app.domain.repository import Repository

MAX_LIMIT = 500
MAX_NAME = 80


class LogSourceKind(StrEnum):
    GCP = "gcp"  # Google Cloud Logging, one project per source


# the configuration each kind needs, all required text
SOURCE_CONFIG: dict[LogSourceKind, tuple[str, ...]] = {
    LogSourceKind.GCP: ("project",),
}


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


def _kind(value) -> LogSourceKind:
    try:
        return LogSourceKind(value)
    except ValueError:
        raise ValidationError(f"Unknown kind of log source: '{value}'") from None


def _check_name(name: str) -> None:
    if not name.strip():
        raise ValidationError("Name is required")
    if len(name) > MAX_NAME:
        raise ValidationError(f"Name must be at most {MAX_NAME} characters")


def _config(kind: LogSourceKind, config: Mapping[str, str] | None) -> dict[str, str]:
    """The kind's configuration, trimmed; anything else is dropped."""
    given = config or {}
    return {
        key: _clean(value) if isinstance(value := given.get(key), str) else "" for key in SOURCE_CONFIG[kind]
    }


def _check_config(kind: LogSourceKind, config: Mapping[str, str]) -> None:
    for key in SOURCE_CONFIG[kind]:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{key.capitalize()} is required")
    unknown = set(config) - set(SOURCE_CONFIG[kind])
    if unknown:
        raise ValidationError(f"Unknown setting '{sorted(unknown)[0]}'")


@dataclass
class LogSource:
    """Where logs are read from: a kind of backend plus its configuration (for
    GCP, the project). Filters point at one; its kind cannot change after
    creation, since their expressions are written in that kind's language.
    """

    id: str
    name: str
    kind: LogSourceKind
    config: dict[str, str]
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        self.kind = _kind(self.kind)
        _check_config(self.kind, self.config)
        _check_name(self.name)

    @classmethod
    def new(cls, name: str, kind: LogSourceKind | str, config: Mapping[str, str]) -> "LogSource":
        source_kind = _kind(kind)
        return cls(id=new_id(), name=_clean(name), kind=source_kind, config=_config(source_kind, config))

    def update(self, name: str, config: Mapping[str, str]) -> None:
        """Rename and replace the configuration; id, kind and created_at stay."""
        clean_name, clean_config = _clean(name), _config(self.kind, config)
        replace(self, name=clean_name, config=clean_config)  # validates before anything changes
        self.name, self.config = clean_name, clean_config

    def same_place(self, kind: LogSourceKind | str, config: Mapping[str, str]) -> bool:
        """Whether `kind` + `config` describe this very source (imports reuse it)."""
        try:
            other = _kind(kind)
        except ValidationError:
            return False
        return other is self.kind and _config(other, config) == self.config


type RefinementValue = str | int | float | bool


@dataclass(frozen=True)
class Refinement:
    """`path` equal (or, negated, not equal) to `value`, ANDed onto a query.

    `path` is the field's segments as they appear in the entry record, e.g.
    ("labels", "k8s-pod/app"); the provider spells it in its own language.
    """

    path: tuple[str, ...]
    value: RefinementValue
    negate: bool = False

    def __post_init__(self):
        path = tuple(self.path)
        if not path or not all(isinstance(part, str) and part.strip() for part in path):
            raise ValidationError("A refinement needs a field")
        if not isinstance(self.value, str | int | float | bool):
            raise ValidationError("A refinement needs a value")
        object.__setattr__(self, "path", path)


@dataclass
class LogQuery:
    """Parameters for a single log read, saved or ad hoc, on some source."""

    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = 50
    refinements: tuple[Refinement, ...] = ()

    def __post_init__(self):
        self.expression = _clean(self.expression)
        self.freshness = _freshness(self.freshness)
        self.refinements = tuple(self.refinements)
        _check_limit(self.limit)

    def refined(self, refinements: Sequence[Refinement]) -> "LogQuery":
        return replace(self, refinements=(*self.refinements, *refinements))


def _fields(name, source_id, expression, freshness, limit, description, line_format) -> dict:
    return dict(
        name=_clean(name),
        source_id=_clean(source_id),
        expression=_clean(expression),
        freshness=_freshness(freshness),
        limit=limit,
        description=_clean(description),
        line_format=_clean(line_format),
    )


@dataclass
class LogFilter:
    """A saved query on a log source, re-run with one click.

    Invariants: a name, a source and a limit within 1..MAX_LIMIT (that the
    source exists is the service's to check). Build with `new`, edit with
    `update`; both trim the text fields.
    """

    id: str
    name: str
    source_id: str
    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = 50
    description: str = ""
    line_format: str = ""
    position: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        self.freshness = _freshness(self.freshness)
        _check_name(self.name)
        if not self.source_id.strip():
            raise ValidationError("A log source is required")
        _check_limit(self.limit)

    @classmethod
    def new(
        cls,
        name: str,
        source_id: str,
        expression: str = "",
        freshness: Freshness | str = Freshness.H1,
        limit: int = 50,
        description: str = "",
        line_format: str = "",
        position: int = 0,
    ) -> "LogFilter":
        values = _fields(name, source_id, expression, freshness, limit, description, line_format)
        return cls(id=new_id(), position=position, **values)

    def update(
        self,
        name: str,
        source_id: str,
        expression: str = "",
        freshness: Freshness | str = Freshness.H1,
        limit: int = 50,
        description: str = "",
        line_format: str = "",
    ) -> None:
        """Replace the editable fields; id, position and created_at stay."""
        values = _fields(name, source_id, expression, freshness, limit, description, line_format)
        replace(self, **values)  # validates the result before anything changes
        for field_name, value in values.items():
            setattr(self, field_name, value)

    def query(self) -> LogQuery:
        return LogQuery(
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


class LogSourceRepository(Repository[LogSource]):
    """Log sources, by id."""


class LogProvider(ABC):
    """Reads log entries from one kind of backend (one per LogSourceKind)."""

    def is_available(self) -> bool:
        """Whether the backend is ready to serve queries (health check)."""
        return True

    @abstractmethod
    def expression(self, query: LogQuery) -> str:
        """The query's expression with its refinements ANDed on, in the
        backend's language: what a refined view saves as a new filter."""

    @abstractmethod
    def query(self, source: LogSource, query: LogQuery) -> list[LogEntry]: ...

    @abstractmethod
    def describe(self, source: LogSource, query: LogQuery) -> str:
        """Human-readable equivalent command, shown in the UI for copy/paste."""

    def console_url(self, source: LogSource, query: LogQuery) -> str:
        """The query in the backend's own web console; empty when there is none."""
        return ""
