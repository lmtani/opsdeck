"""Deploy markers: the window rules, the merge of App Engine promotions, the
audit-log parser (fixtures shaped like real Cloud Run and App Engine audit
entries, trimmed and with made-up names) and the use case."""

from dataclasses import fields
from datetime import timedelta

import pytest

from opsdeck.application.deploys import DeployService
from opsdeck.domain.deploys import (
    Deploy,
    DeployKind,
    DeployPlatform,
    DeployTarget,
    DeployWindow,
    TrafficShare,
    merge_promotions,
)
from opsdeck.domain.errors import NotFoundError, ValidationError
from opsdeck.domain.logs import LogEntry, LogSource, LogSourceKind
from opsdeck.infrastructure.logs.gcp.deploys import deploy_targets, deploys_filter, parse_deploys
from tests.fakes import FakeDeployFeed, InMemorySourceRepository, ts

API = DeployTarget(DeployPlatform.CLOUD_RUN, "api")
JOB = DeployTarget(DeployPlatform.CLOUD_RUN_JOB, "nightly")
WEB = DeployTarget(DeployPlatform.APP_ENGINE, "default")
WINDOW = DeployWindow((API, JOB, WEB), ts("2026-10-30T00:00:00Z"), ts("2026-10-31T00:00:00Z"))
CI = "deployer@proj.iam.gserviceaccount.com"


def audit(timestamp, method, resource_type, labels, request=None, metadata=None, operation=None, **extra):
    payload = {
        "methodName": method,
        "serviceName": method.split(".")[2] + ".googleapis.com",
        "authenticationInfo": {"principalEmail": CI},
        "status": extra.pop("status", {}),
    }
    if request is not None:
        payload["request"] = request
    if metadata is not None:
        payload["metadata"] = metadata
    item = {
        "timestamp": timestamp,
        "severity": extra.pop("severity", "NOTICE"),
        "resource": {"type": resource_type, "labels": labels},
        "protoPayload": payload,
    }
    if operation:
        item["operation"] = operation
    return item


def run_v1(timestamp, service="api", revision="api-00042-abc", traffic=None):
    spec = {
        "template": {
            "metadata": {"name": revision},
            "spec": {"containers": [{"image": "us-docker.pkg.dev/p/r/api:3493ddd4"}]},
        },
        "traffic": traffic
        or [{"latestRevision": True, "percent": 100}, {"revisionName": revision, "tag": "main"}],
    }
    return audit(
        timestamp,
        "google.cloud.run.v1.Services.ReplaceService",
        "cloud_run_revision",
        {"service_name": service, "revision_name": "", "location": "us-east1"},
        request={"service": {"spec": spec}},
    )


def gae(timestamp, method, version, first, op, metadata=None, **extra):
    return audit(
        timestamp,
        f"google.appengine.v1.{method}",
        "gae_app",
        {"module_id": "default", "version_id": version if method.startswith("Versions") else ""},
        metadata=metadata,
        operation={"id": op, "first": True} if first else {"id": op, "last": True},
        **extra,
    )


SECRET_VERSION = {"version": {"envVariables": {"AWS_SECRET": "do-not-leak"}}}


def test_the_window_is_checked():
    with pytest.raises(ValidationError, match="start before"):
        DeployWindow((API,), ts("2026-10-31T00:00:00Z"), ts("2026-10-30T00:00:00Z"))
    with pytest.raises(ValidationError, match="8 days"):
        DeployWindow((API,), ts("2026-10-01T00:00:00Z"), ts("2026-10-20T00:00:00Z"))
    targets = tuple(DeployTarget("cloud_run", f"s{i}") for i in range(21))
    with pytest.raises(ValidationError, match="At most 20"):
        DeployWindow(targets, ts("2026-10-30T00:00:00Z"), ts("2026-10-31T00:00:00Z"))
    with pytest.raises(ValidationError, match="Unknown platform"):
        DeployTarget("lambda", "x")


def test_cloud_run_v1_deploy():
    [deploy] = parse_deploys([run_v1("2026-10-30T20:08:28Z")], WINDOW)
    assert deploy == Deploy(
        target=API,
        at=ts("2026-10-30T20:08:28Z"),
        kind=DeployKind.DEPLOY,
        revision="api-00042-abc",
        image="us-docker.pkg.dev/p/r/api:3493ddd4",
        actor=CI,
    )


def test_cloud_run_traffic_split_without_the_latest_is_a_traffic_change():
    split = [
        {"revisionName": "api-00041-old", "percent": 90},
        {"revisionName": "api-00042-abc", "percent": 10},
    ]
    [deploy] = parse_deploys([run_v1("2026-10-30T10:00:00Z", traffic=split)], WINDOW)
    assert deploy.kind is DeployKind.TRAFFIC
    assert deploy.traffic == (TrafficShare("api-00041-old", 90), TrafficShare("api-00042-abc", 10))


def test_cloud_run_v2_and_jobs():
    v2 = audit(
        "2026-10-30T19:41:43Z",
        "google.cloud.run.v2.Services.UpdateService",
        "cloud_run_revision",
        {"service_name": "api"},
        request={
            "service": {
                "template": {"containers": [{"image": "img:sha-b29d2f1"}]},
                "traffic": [{"percent": 100, "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"}],
            }
        },
    )
    job = audit(
        "2026-10-30T15:02:32Z",
        "google.cloud.run.v1.Jobs.ReplaceJob",
        "cloud_run_job",
        {"job_name": "nightly"},
        request={
            "job": {
                "spec": {"template": {"spec": {"template": {"spec": {"containers": [{"image": "j:1"}]}}}}}
            }
        },
    )
    deploys = parse_deploys([job, v2], WINDOW)
    assert [(d.target, d.image, d.kind, d.traffic) for d in deploys] == [
        (API, "img:sha-b29d2f1", DeployKind.DEPLOY, ()),
        (JOB, "j:1", DeployKind.DEPLOY, ()),
    ]


def test_an_app_engine_deploy_and_its_promotion_read_as_one_live_deploy():
    entries = [
        gae("2026-10-30T19:06:00Z", "Versions.CreateVersion", "main-5421", True, "op1", SECRET_VERSION),
        gae("2026-10-30T19:08:00Z", "Versions.CreateVersion", "main-5421", False, "op1"),
        gae(
            "2026-10-30T19:08:20Z",
            "Services.UpdateService",
            "",
            True,
            "op2",
            {"service": {"split": {"allocations": {"main-5421": 1}}}, "updateMask": "split"},
        ),
        gae("2026-10-30T19:08:30Z", "Services.UpdateService", "", False, "op2"),
    ]
    [deploy] = parse_deploys(entries, WINDOW)
    assert (deploy.kind, deploy.revision, deploy.live) == (DeployKind.DEPLOY, "main-5421", True)
    assert deploy.at == ts("2026-10-30T19:08:30Z")  # when the traffic moved
    assert deploy.traffic == (TrafficShare("main-5421", 100),)
    # only the picked fields leave the parser, never the version's environment
    assert "do-not-leak" not in repr([getattr(deploy, f.name) for f in fields(deploy)])


def test_an_unpromoted_version_is_not_live_and_a_later_traffic_move_stands_alone():
    entries = [
        gae("2026-10-30T11:09:17Z", "Versions.CreateVersion", "feat-x", True, "op1"),
        gae("2026-10-30T11:10:55Z", "Versions.CreateVersion", "feat-x", False, "op1"),
        gae(
            "2026-10-30T15:25:34Z",
            "Services.UpdateService",
            "",
            True,
            "op2",
            {"service": {"split": {"allocations": {"feat-x": 0.25, "main-1": 0.75}}}},
        ),
        gae("2026-10-30T15:25:41Z", "Services.UpdateService", "", False, "op2"),
    ]
    traffic, created = parse_deploys(entries, WINDOW)
    assert (created.revision, created.live) == ("feat-x", False)
    assert traffic.kind is DeployKind.TRAFFIC
    assert traffic.traffic == (TrafficShare("main-1", 75), TrafficShare("feat-x", 25))


def test_a_failed_app_engine_deploy():
    entries = [
        gae("2026-10-30T10:29:00Z", "Versions.CreateVersion", "main-5401", True, "op1"),
        gae(
            "2026-10-30T10:31:12Z",
            "Versions.CreateVersion",
            "main-5401",
            False,
            "op1",
            severity="ERROR",
            status={"code": 13, "message": "Failed exchange in Gaia\nstack…"},
        ),
    ]
    [deploy] = parse_deploys(entries, WINDOW)
    assert deploy.failed and deploy.message == "Failed exchange in Gaia"


def test_only_wanted_targets_inside_the_window_are_kept():
    entries = [
        run_v1("2026-10-30T12:00:00Z", service="other"),
        run_v1("2026-10-29T23:59:00Z"),  # read for the lookback, before the window
        run_v1("2026-10-30T12:00:00Z"),
        run_v1("2026-10-30T12:00:00Z"),  # reported twice
        {"timestamp": "bad"},
    ]
    assert len(parse_deploys(entries, WINDOW)) == 1


def test_merge_promotions_needs_the_traffic_soon_after():
    created = Deploy(WEB, ts("2026-10-30T10:00:00Z"), DeployKind.DEPLOY, revision="v1", live=False)
    late = Deploy(WEB, ts("2026-10-30T11:00:00Z"), DeployKind.TRAFFIC, traffic=(TrafficShare("v1", 100),))
    assert merge_promotions([created, late]) == [created, late]


def test_targets_come_from_cloud_run_and_app_engine_entries():
    entries = [
        LogEntry("t", "INFO", "", "cloud_run_revision", {"service_name": "api"}),
        LogEntry("t", "INFO", "", "gae_app", {"module_id": "default", "version_id": "v1"}),
        LogEntry("t", "INFO", "", "cloud_run_revision", {"service_name": "api"}),
        LogEntry("t", "INFO", "", "cloud_run_job", {"job_name": "nightly"}),
        LogEntry("t", "INFO", "", "k8s_container", {"container_name": "x"}),
        LogEntry("t", "INFO", "", "gae_app", {"module_id": ""}),
    ]
    assert deploy_targets(entries) == [API, WEB, JOB]


def test_the_filter_reads_the_audit_log_of_each_platform():
    text = deploys_filter("proj", WINDOW)
    assert text.startswith('logName="projects/proj/logs/cloudaudit.googleapis.com%2Factivity" AND ')
    assert 'timestamp>="2026-10-29T23:30:00.000000Z"' in text  # the operation lookback
    assert 'resource.type="cloud_run_revision" AND resource.labels.service_name=("api")' in text
    assert 'resource.labels.job_name=("nightly")' in text
    assert (
        '"google.appengine.v1.Versions.CreateVersion" OR "google.appengine.v1.Services.UpdateService"' in text
    )


def test_the_service_reads_the_feed_of_the_source_kind():
    sources = InMemorySourceRepository()
    source = LogSource.new("prod", LogSourceKind.GCP, {"project": "proj"})
    sources.save(source)
    deploy = Deploy(API, ts("2026-10-30T12:00:00Z"), DeployKind.DEPLOY)
    feed = FakeDeployFeed([deploy])
    service = DeployService({LogSourceKind.GCP: feed}, sources)
    start = ts("2026-10-30T00:00:00Z")
    assert service.deploys(source.id, [API, API], start, start + timedelta(days=1)) == [deploy]
    assert feed.windows[0].targets == (API,)
    assert service.deploys(source.id, [], start, start + timedelta(days=1)) == []
    assert len(feed.windows) == 1  # nothing to ask without targets
    assert DeployService({}, sources).deploys(source.id, [API], start, start + timedelta(hours=1)) == []
    with pytest.raises(NotFoundError):
        service.deploys("missing", [API], start, start + timedelta(hours=1))
