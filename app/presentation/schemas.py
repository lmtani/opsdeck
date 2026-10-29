"""Pydantic request/response models for the HTTP API.

The *Out models are the response contract: they read the entity's
attributes by name (from_attributes), so a field is only sent when it is
declared here, and the models with a shape of their own (events, activity,
contributions) are built explicitly.
"""

from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.application.sharing import Bundle, ImportAction, ImportStatus, ItemType
from app.domain.github import (
    ContributionKind,
    EventKind,
    EventSource,
    GithubActivity,
    GithubContributions,
    GithubEvent,
    ItemState,
    ReviewState,
    SubjectType,
)
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
    github_polling: bool = True


class SettingsUpdate(BaseModel):
    """A partial update: fields left out keep their saved value."""

    theme: str | None = None
    github_polling: bool | None = None


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


class GithubTokenOut(BaseModel):
    """What the configured GitHub token can do; problems are user-facing advice."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    login: str = ""
    scopes: list[str] | None = None
    usable: bool
    problems: list[str] = []
    notes: list[str] = []


class FeedSpecOut(BaseModel):
    """A feed specification as the server normalizes it."""

    model_config = ConfigDict(from_attributes=True)

    org: str
    repos: list[str] = []
    event_kinds: list[EventKind] = []
    hide_own: bool = False
    hide_bots: bool = True


class EventSubjectModel(BaseModel):
    """What an event is about: the frontend sends it back as is to read the
    event's full context (POST /api/github/events/context)."""

    model_config = ConfigDict(from_attributes=True)

    type: SubjectType
    repo: str
    number: int = 0
    item_id: int = 0


class ThreadCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author: str
    author_avatar: str = ""
    created_at: datetime | None = None
    body_html: str = ""  # sanitized server-side
    url: str = ""
    path: str = ""
    line: int | None = None
    diff_hunk: str = ""


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    color: str = ""  # six hex digits or empty: safe to use in a style


class ReviewerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    login: str
    state: ReviewState | None = None  # None: review requested, not given yet


class PullStatsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    commits: int = 0
    head_ref: str = ""
    base_ref: str = ""
    labels: list[LabelOut] = []
    reviewers: list[ReviewerOut] = []


class EventContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subject: EventSubjectModel
    url: str = ""
    author: str = ""
    author_avatar: str = ""
    created_at: datetime | None = None
    title: str = ""
    state: ItemState | None = None
    review_state: ReviewState | None = None
    body_html: str = ""  # sanitized server-side: safe to set as innerHTML
    path: str = ""
    line: int | None = None
    diff_hunk: str = ""
    thread: list[ThreadCommentOut] = []
    pull: PullStatsOut | None = None


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
    subject: EventSubjectModel | None = None  # absent: nothing more to open

    @classmethod
    def from_entity(cls, event: GithubEvent) -> "GithubEventOut":
        built = ("details", "subject")
        values = {name: getattr(event, name) for name in cls.model_fields if name not in built}
        return cls(
            details=asdict(event.details) if event.details is not None else None,
            subject=EventSubjectModel.model_validate(event.subject) if event.subject else None,
            **values,
        )


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


class GithubContributionsIn(BaseModel):
    days: int = 28
    tz: str = "UTC"  # IANA name of the browser's time zone: days are local days


class ContributionDayIn(GithubContributionsIn):
    """One cell of the grid: send the same days and tz as the grid's query."""

    login: str = Field(min_length=1)
    day: date


class ContributionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    login: str
    kind: ContributionKind
    repo: str
    at: datetime
    number: int = 0
    title: str = ""
    url: str = ""
    review_state: ReviewState | None = None


class DailyContributionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    login: str
    day: date
    kind: ContributionKind
    repo: str
    count: int


class GithubContributionsOut(BaseModel):
    since: date
    until: date
    counts: list[DailyContributionOut]
    avatars: dict[str, str] = {}
    viewer: str = ""
    fetched_at: datetime
    warnings: list[str] = []
    rate_remaining: int | None = None
    rate_limit: int | None = None

    @classmethod
    def from_entity(cls, result: GithubContributions) -> "GithubContributionsOut":
        return cls(
            since=result.since,
            until=result.until,
            counts=[DailyContributionOut.model_validate(count) for count in result.counts],
            avatars=result.avatars,
            viewer=result.viewer,
            fetched_at=result.fetched_at,
            warnings=result.warnings,
            rate_remaining=result.quota.remaining if result.quota else None,
            rate_limit=result.quota.limit if result.quota else None,
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


# ----- Sharing ----------------------------------------------------------------

MAX_BUNDLE_ITEMS = 500


class BundleModel(BaseModel):
    """The export file. Items stay plain objects: each one is checked on its
    own (POST /api/share/check), so one bad item does not reject the file."""

    opsdeck: int = 0  # format version; 0 = not an OpsDeck export
    exported_at: datetime | None = None
    links: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_BUNDLE_ITEMS)
    log_filters: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_BUNDLE_ITEMS)
    github_feeds: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_BUNDLE_ITEMS)

    def to_bundle(self) -> Bundle:
        return Bundle(
            links=self.links,
            log_filters=self.log_filters,
            github_feeds=self.github_feeds,
            version=self.opsdeck,
            exported_at=self.exported_at,
        )

    @classmethod
    def from_bundle(cls, bundle: Bundle) -> "BundleModel":
        return cls(
            opsdeck=bundle.version,
            exported_at=bundle.exported_at,
            links=bundle.links,
            log_filters=bundle.log_filters,
            github_feeds=bundle.github_feeds,
        )


class ShareExportIn(BaseModel):
    links: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    feeds: list[str] = Field(default_factory=list)


class ImportCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: ItemType
    index: int
    name: str
    detail: str
    status: ImportStatus
    error: str = ""
    command: str = ""


class ImportChoiceIn(BaseModel):
    type: ItemType
    index: int = Field(ge=0)
    action: ImportAction


class ShareImportIn(BaseModel):
    bundle: BundleModel
    choices: list[ImportChoiceIn]


class ImportedItemOut(BaseModel):
    type: ItemType
    id: str


class ImportResultOut(BaseModel):
    created: list[ImportedItemOut]
    replaced: list[ImportedItemOut]
