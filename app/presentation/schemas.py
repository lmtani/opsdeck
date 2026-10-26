"""Pydantic request/response models for the HTTP API.

The *Out models are the response contract: they read the entity's
attributes by name (from_attributes), so a field is only sent when it is
declared here, and the two models with a shape of their own (events,
activity) are built explicitly.
"""

from dataclasses import asdict
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.github import EventKind, EventSource, GithubActivity, GithubEvent, ItemState
from app.domain.links import LinkKind
from app.domain.logs import Freshness


class LinkIn(BaseModel):
    title: str = Field(min_length=1)
    url: str = ""
    kind: LinkKind = LinkKind.URL
    command: str = ""
    description: str = ""
    image_url: str = ""
    category: str = ""
    accent: str = Field(default="", max_length=24)


class LinkOut(LinkIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int = 0
    created_at: datetime


class CommandResultOut(BaseModel):
    message: str


class ReorderIn(BaseModel):
    ids: list[str]


class LogFilterIn(BaseModel):
    name: str = Field(min_length=1)
    project: str = Field(min_length=1)
    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = Field(default=50, ge=1, le=500)
    description: str = ""
    line_format: str = Field(default="", max_length=240)


class LogFilterOut(LogFilterIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int = 0
    created_at: datetime


class LogQueryIn(BaseModel):
    """An ad-hoc query; saved filters run through POST /api/filters/{id}/query."""

    project: str = Field(min_length=1)
    expression: str = ""
    freshness: Freshness = Freshness.H1
    limit: int = Field(default=50, ge=1, le=500)


class LogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: str
    severity: str
    summary: str
    resource_type: str = ""
    resource_labels: dict = {}
    payload: dict | str | None = None
    log_name: str = ""
    insert_id: str = ""
    trace: str = ""
    span_id: str = ""
    operation_id: str = ""
    fields: dict = {}


class LogQueryOut(BaseModel):
    command: str
    entries: list[LogEntryOut]


class SettingsModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    theme: str = "midnight"


class ImageOut(BaseModel):
    url: str


class HealthOut(BaseModel):
    status: str = "ok"
    logs: bool  # the log provider (gcloud CLI or Cloud Logging API) is usable
    github: bool = False  # GitHub credentials are configured


class GithubFeedIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    org: str = Field(min_length=1, max_length=39)
    repos: list[str] = Field(default_factory=list, max_length=40)
    event_kinds: list[EventKind] = Field(default_factory=list)
    hide_own: bool = False
    hide_bots: bool = True
    description: str = Field(default="", max_length=160)


class GithubFeedOut(GithubFeedIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    last_seen_at: datetime | None = None
    position: int = 0
    created_at: datetime


class GithubSeenIn(BaseModel):
    seen_at: datetime | None = None  # None (or "") = now

    @field_validator("seen_at", mode="before")
    @classmethod
    def _empty_is_none(cls, value):
        return None if value == "" else value


class GithubQueryIn(BaseModel):
    """An ad-hoc feed; saved feeds run through POST /api/github/feeds/{id}/query."""

    org: str = Field(min_length=1)
    repos: list[str] = Field(default_factory=list, max_length=40)
    event_kinds: list[EventKind] = Field(default_factory=list)
    hide_own: bool = False
    hide_bots: bool = True


class FeedSpecOut(BaseModel):
    """A feed specification as the server normalizes it."""

    model_config = ConfigDict(from_attributes=True)

    org: str
    repos: list[str] = []
    event_kinds: list[EventKind] = []
    hide_own: bool = False
    hide_bots: bool = True


class GithubEventOut(BaseModel):
    id: str
    kind: EventKind
    type: str
    repo: str
    actor: str
    created_at: datetime
    summary: str
    url: str = ""
    actor_avatar: str = ""
    action: str = ""
    ref: str = ""
    number: int = 0
    title: str = ""
    state: ItemState | None = None
    body: str = ""
    is_pull: bool = False
    source: EventSource = EventSource.EVENTS
    details: dict | None = None  # shape depends on the event: push, PR, review, code comment, ref
    count: int = 1
    is_new: bool = False

    @classmethod
    def from_entity(cls, event: GithubEvent) -> "GithubEventOut":
        values = {name: getattr(event, name) for name in cls.model_fields if name != "details"}
        return cls(details=asdict(event.details) if event.details is not None else None, **values)


class GithubActivityOut(BaseModel):
    events: list[GithubEventOut]
    viewer: str = ""
    fetched_at: datetime
    warnings: list[str] = []
    coverage_start: datetime | None = None
    rate_remaining: int | None = None
    rate_limit: int | None = None
    unseen: int = 0

    @classmethod
    def from_entity(cls, activity: GithubActivity) -> "GithubActivityOut":
        return cls(
            events=[GithubEventOut.from_entity(event) for event in activity.events],
            viewer=activity.viewer,
            fetched_at=activity.fetched_at,
            warnings=activity.warnings,
            coverage_start=activity.coverage_start,
            unseen=activity.unseen,
            rate_remaining=activity.quota.remaining if activity.quota else None,
            rate_limit=activity.quota.limit if activity.quota else None,
        )


class GithubRepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    full_name: str
    private: bool = False
    archived: bool = False
    description: str = ""
    pushed_at: datetime | None = None
    url: str = ""
