"""Deploys context: changes to the services whose logs a query shows, drawn
over its results so an error that starts right after a deploy stands out.

A DeployTarget names a deployable thing (a Cloud Run service or job, an App
Engine service); the DeployFeed of a log source's kind says which targets a
query's entries came from and which deploys those targets had in a window.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from opsdeck.domain.errors import ValidationError
from opsdeck.domain.logs import LogEntry, LogSource

MAX_TARGETS = 20
MAX_SPAN = timedelta(days=8)  # the longest window a filter shows is 7 days
MAX_NAME = 100


class DeployPlatform(StrEnum):
    CLOUD_RUN = "cloud_run"  # a Cloud Run service
    CLOUD_RUN_JOB = "cloud_run_job"
    APP_ENGINE = "app_engine"


class DeployKind(StrEnum):
    DEPLOY = "deploy"  # new code or configuration: a revision, a version, a job update
    TRAFFIC = "traffic"  # traffic moved between existing revisions or versions


@dataclass(frozen=True)
class DeployTarget:
    platform: DeployPlatform
    name: str

    def __post_init__(self):
        if not isinstance(self.platform, DeployPlatform):
            object.__setattr__(self, "platform", _platform(self.platform))
        if not self.name or len(self.name) > MAX_NAME:
            raise ValidationError(f"A service name must have 1 to {MAX_NAME} characters")


def _platform(value) -> DeployPlatform:
    try:
        return DeployPlatform(value)
    except ValueError:
        raise ValidationError(f"Unknown platform: '{value}'") from None


@dataclass(frozen=True)
class TrafficShare:
    target: str  # the revision or version, "" for the latest revision
    percent: int


@dataclass(frozen=True)
class Deploy:
    target: DeployTarget
    at: datetime  # when it finished (for a long-running operation) or was made
    kind: DeployKind
    revision: str = ""  # the revision or version created, when known
    image: str = ""  # the container image, often tagged with the commit
    actor: str = ""  # the account that made it: a person or a CI service account
    failed: bool = False
    message: str = ""  # why it failed
    traffic: tuple[TrafficShare, ...] = ()  # the split a traffic change set
    live: bool = True  # whether it sent traffic to new code (an unpromoted version does not)


@dataclass(frozen=True)
class DeployWindow:
    """The checked request: some targets over [start, end)."""

    targets: tuple[DeployTarget, ...]
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValidationError("The window needs times with a time zone")
        if self.start >= self.end:
            raise ValidationError("The window must start before it ends")
        if self.end - self.start > MAX_SPAN:
            raise ValidationError("The window can span at most 8 days")
        if len(self.targets) > MAX_TARGETS:
            raise ValidationError(f"At most {MAX_TARGETS} services at a time")


def collapse(deploys: Sequence[Deploy]) -> list[Deploy]:
    """Newest first, one per target and instant (a feed may report a change
    twice, e.g. from both ends of an operation)."""
    seen: set[tuple] = set()
    kept = []
    for deploy in sorted(deploys, key=lambda item: item.at, reverse=True):
        key = (deploy.target, deploy.at, deploy.kind, deploy.revision)
        if key not in seen:
            seen.add(key)
            kept.append(deploy)
    return kept


PROMOTION_GAP = timedelta(minutes=10)


def merge_promotions(deploys: Sequence[Deploy]) -> list[Deploy]:
    """A version created and then given all or most of the traffic within
    PROMOTION_GAP reads as one live deploy (the traffic change is dropped);
    a version nobody moved traffic to stays a deploy that is not live."""
    promotions: dict[tuple[DeployTarget, str], Deploy] = {}
    for deploy in deploys:
        if deploy.kind is DeployKind.TRAFFIC and not deploy.failed and deploy.traffic:
            top = max(deploy.traffic, key=lambda share: share.percent)
            if top.percent >= 50:
                promotions.setdefault((deploy.target, top.target), deploy)
    merged, used = [], set()
    for deploy in deploys:
        promotion = promotions.get((deploy.target, deploy.revision)) if deploy.revision else None
        if (
            deploy.kind is DeployKind.DEPLOY
            and not deploy.failed
            and promotion is not None
            and timedelta(0) <= promotion.at - deploy.at <= PROMOTION_GAP
        ):
            merged.append(replace(deploy, at=promotion.at, traffic=promotion.traffic, live=True))
            used.add(id(promotion))
        else:
            merged.append(deploy)
    return [deploy for deploy in merged if id(deploy) not in used]


class DeployFeed(ABC):
    """The deploys of one kind of log source (one per LogSourceKind)."""

    @abstractmethod
    def targets(self, entries: Sequence[LogEntry]) -> list[DeployTarget]:
        """The deployable things these entries came from, without repeats."""

    @abstractmethod
    def deploys(self, source: LogSource, window: DeployWindow) -> list[Deploy]:
        """The deploys of the window's targets that finished inside it, newest first."""
