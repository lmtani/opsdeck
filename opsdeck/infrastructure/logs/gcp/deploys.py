"""Deploys of Cloud Run and App Engine, read from the project's Admin Activity
audit log: always on, free and readable with roles/logging.viewer, so they
need nothing the Logs tab does not already have. Pure functions.

- Cloud Run writes one entry per change (`...Services.ReplaceService` from
  gcloud v1 clients, `...v2.Services.UpdateService`, the `Jobs` methods),
  with the new spec in `protoPayload.request`.
- App Engine changes are long-running operations with a first and a last
  entry, sharing `operation.id`: `Versions.CreateVersion` (a deploy; the new
  version takes traffic only when promoted, which is a separate
  `Services.UpdateService` whose first entry holds the split), finished at
  the last entry, with an ERROR severity and a `status` when it failed.

Audit entries carry the whole request, an App Engine version's environment
variables (secrets included) among it: only the fields below are read, and
nothing else of the entry leaves this module.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from opsdeck.domain.common import parse_timestamp
from opsdeck.domain.deploys import (
    Deploy,
    DeployKind,
    DeployPlatform,
    DeployTarget,
    DeployWindow,
    TrafficShare,
    collapse,
    merge_promotions,
)
from opsdeck.domain.logs import LogEntry, TimeRange
from opsdeck.infrastructure.logs.gcp.expression import range_clause, string_literal

# how far before the window an operation's first entry may be (an App Engine
# deploy takes a few minutes; its traffic split is only in the first entry)
OPERATION_LOOKBACK = timedelta(minutes=30)
MAX_MESSAGE = 200

_RUN_SERVICE_METHODS = (
    "google.cloud.run.v1.Services.ReplaceService",
    "google.cloud.run.v1.Services.CreateService",
    "google.cloud.run.v2.Services.UpdateService",
    "google.cloud.run.v2.Services.CreateService",
)
_RUN_JOB_METHODS = (
    "google.cloud.run.v1.Jobs.ReplaceJob",
    "google.cloud.run.v1.Jobs.CreateJob",
    "google.cloud.run.v2.Jobs.UpdateJob",
    "google.cloud.run.v2.Jobs.CreateJob",
)
_GAE_CREATE_VERSION = "google.appengine.v1.Versions.CreateVersion"
_GAE_UPDATE_SERVICE = "google.appengine.v1.Services.UpdateService"

# per platform: the resource type of its log entries and audit entries, the
# label naming the target, and the audit methods that change it
_PLATFORMS = {
    DeployPlatform.CLOUD_RUN: ("cloud_run_revision", "service_name", _RUN_SERVICE_METHODS),
    DeployPlatform.CLOUD_RUN_JOB: ("cloud_run_job", "job_name", _RUN_JOB_METHODS),
    DeployPlatform.APP_ENGINE: ("gae_app", "module_id", (_GAE_CREATE_VERSION, _GAE_UPDATE_SERVICE)),
}
_BY_RESOURCE = {resource: platform for platform, (resource, _, _) in _PLATFORMS.items()}


def deploy_targets(entries: Iterable[LogEntry]) -> list[DeployTarget]:
    """The Cloud Run services and jobs and App Engine services the entries
    came from, in order of first appearance."""
    found: dict[DeployTarget, None] = {}
    for entry in entries:
        platform = _BY_RESOURCE.get(entry.resource_type)
        if platform is None:
            continue
        name = (entry.resource_labels or {}).get(_PLATFORMS[platform][1])
        if isinstance(name, str) and name:
            found.setdefault(DeployTarget(platform, name), None)
    return list(found)


def _any_of(values: Iterable[str]) -> str:
    return "(" + " OR ".join(string_literal(value) for value in values) + ")"


def deploys_filter(project: str, window: DeployWindow) -> str:
    """The audit entries of the window's targets, from a little before it so
    that operations finishing inside it are whole."""
    names: dict[DeployPlatform, list[str]] = {}
    for target in window.targets:
        names.setdefault(target.platform, []).append(target.name)
    per_platform = []
    for platform, targets in names.items():
        resource, label, methods = _PLATFORMS[platform]
        per_platform.append(
            f"(protoPayload.methodName={_any_of(methods)} AND resource.type={string_literal(resource)}"
            f" AND resource.labels.{label}={_any_of(targets)})"
        )
    log_name = f"projects/{project}/logs/cloudaudit.googleapis.com%2Factivity"
    time_range = range_clause(TimeRange(window.start - OPERATION_LOOKBACK, window.end))
    return f"logName={string_literal(log_name)} AND {time_range} AND ({' OR '.join(per_platform)})"


# ----- parsing -------------------------------------------------------------


def _get(record, *path, default=None):
    for key in path:
        if not isinstance(record, dict):
            return default
        record = record.get(key)
    return default if record is None else record


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _at(item: dict) -> datetime:
    at = parse_timestamp(item.get("timestamp"))
    if at is None:  # parse_deploys only keeps entries with a timestamp
        raise ValueError("audit entry without a timestamp")
    return at


def _actor(item: dict) -> str:
    return _text(_get(item, "protoPayload", "authenticationInfo", "principalEmail"))


def _failure(item: dict) -> tuple[bool, str]:
    status = _get(item, "protoPayload", "status", default={})
    code = status.get("code") if isinstance(status, dict) else None
    failed = item.get("severity") in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY") or bool(code)
    message = _text(status.get("message")) if isinstance(status, dict) else ""
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return failed, first_line[:MAX_MESSAGE]


def _first_image(containers) -> str:
    if isinstance(containers, list) and containers and isinstance(containers[0], dict):
        return _text(containers[0].get("image"))
    return ""


def _run_traffic(targets) -> tuple[TrafficShare, ...]:
    """The split, unless it is the plain "all to the latest revision"."""
    if not isinstance(targets, list):
        return ()
    shares = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        percent = target.get("percent")
        if not isinstance(percent, int) or percent <= 0:
            continue  # a tag without traffic
        latest = target.get("latestRevision") is True or target.get("type", "").endswith("_LATEST")
        name = "" if latest else _text(target.get("revisionName") or target.get("revision"))
        shares.append(TrafficShare(name, percent))
    if len(shares) == 1 and shares[0] == TrafficShare("", 100):
        return ()
    return tuple(shares)


def _cloud_run(item: dict, platform: DeployPlatform, name: str) -> Deploy:
    method = _text(_get(item, "protoPayload", "methodName"))
    request = _get(item, "protoPayload", "request", default={})
    revision = ""
    traffic: tuple[TrafficShare, ...] = ()  # jobs have neither
    if platform is DeployPlatform.CLOUD_RUN_JOB:
        job = request.get("job", {}) if isinstance(request, dict) else {}
        if ".v1." in method:
            containers = _get(job, "spec", "template", "spec", "template", "spec", "containers")
        else:
            containers = _get(job, "template", "template", "containers")
    else:
        service = request.get("service", {}) if isinstance(request, dict) else {}
        if ".v1." in method:
            template = _get(service, "spec", "template", default={})
            containers = _get(template, "spec", "containers")
            revision = _text(_get(template, "metadata", "name"))
            traffic = _run_traffic(_get(service, "spec", "traffic"))
        else:
            template = _get(service, "template", default={})
            containers = template.get("containers") if isinstance(template, dict) else None
            revision = _text(template.get("revision")) if isinstance(template, dict) else ""
            traffic = _run_traffic(service.get("traffic"))
    failed, message = _failure(item)
    # a split that leaves the latest revision without traffic moves traffic around
    kind = DeployKind.TRAFFIC if traffic and all(share.target for share in traffic) else DeployKind.DEPLOY
    return Deploy(
        target=DeployTarget(platform, name),
        at=_at(item),
        kind=kind,
        revision=revision,
        image=_first_image(containers),
        actor=_actor(item),
        failed=failed,
        message=message,
        traffic=traffic,
    )


def _gae_split(first: dict | None) -> tuple[TrafficShare, ...]:
    allocations = _get(first or {}, "protoPayload", "metadata", "service", "split", "allocations")
    if not isinstance(allocations, dict):
        return ()
    shares = [
        TrafficShare(version, round(fraction * 100))
        for version, fraction in allocations.items()
        if isinstance(version, str) and isinstance(fraction, int | float) and fraction > 0
    ]
    return tuple(sorted(shares, key=lambda share: -share.percent))


def _app_engine(first: dict | None, last: dict | None, name: str) -> Deploy | None:
    done = last or first
    if done is None:
        return None
    method = _text(_get(done, "protoPayload", "methodName"))
    failed, message = _failure(done)
    actor = _actor(done) or _actor(first or {})
    at = _at(done)
    target = DeployTarget(DeployPlatform.APP_ENGINE, name)
    if method == _GAE_CREATE_VERSION:
        version = _text(_get(done, "resource", "labels", "version_id"))
        # live only once traffic moves to it (merge_promotions), which may be never
        return Deploy(
            target,
            at,
            DeployKind.DEPLOY,
            revision=version,
            actor=actor,
            failed=failed,
            message=message,
            live=False,
        )
    traffic = _gae_split(first)
    if not traffic and not failed:
        return None  # a service update that did not touch the split (or whose first entry is too old)
    return Deploy(
        target, at, DeployKind.TRAFFIC, actor=actor, failed=failed, message=message, traffic=traffic
    )


def parse_deploys(items: Sequence[dict], window: DeployWindow) -> list[Deploy]:
    """The deploys of the window's targets that finished inside it, newest first."""
    wanted = set(window.targets)
    deploys: list[Deploy] = []
    operations: dict[str, dict[str, dict]] = {}  # App Engine: its first and last entries
    names: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or parse_timestamp(item.get("timestamp")) is None:
            continue
        platform = _BY_RESOURCE.get(_text(_get(item, "resource", "type")))
        if platform is None:
            continue
        name = _text(_get(item, "resource", "labels", _PLATFORMS[platform][1]))
        if not name or DeployTarget(platform, name) not in wanted:
            continue
        if platform is DeployPlatform.APP_ENGINE:
            operation = _get(item, "operation", default={})
            key = _text(operation.get("id")) or f"{item['timestamp']}:{name}"
            names[key] = name
            ends = operations.setdefault(key, {})
            ends["last" if operation.get("last") or not operation.get("first") else "first"] = item
        else:
            deploys.append(_cloud_run(item, platform, name))
    for key, ends in operations.items():
        deploy = _app_engine(ends.get("first"), ends.get("last"), names[key])
        if deploy is not None:
            deploys.append(deploy)
    inside = [deploy for deploy in merge_promotions(deploys) if window.start <= deploy.at < window.end]
    return collapse(inside)
