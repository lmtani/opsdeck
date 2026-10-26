"""In-memory fakes of the domain ports and builders of GitHub events.

Service and API tests use these instead of MySQL, subprocess, gcloud or
HTTP. Each fake records what it was asked, so tests can assert on it.
"""

from app.domain.common import parse_timestamp
from app.domain.errors import GithubNotFoundError
from app.domain.github import (
    ActivitySnapshot,
    CodeCommentDetails,
    EventKind,
    EventSource,
    GithubEvent,
    PullDetails,
    PushDetails,
    ReviewDetails,
    ReviewState,
    GithubEventBatch,
    GithubFeed,
    GithubFeedRepository,
    GithubProvider,
)
from app.domain.links import CommandRunner, ImageStore, Link, LinkRepository
from app.domain.logs import LogEntry, LogFilter, LogFilterRepository, LogProvider, LogQuery
from app.domain.settings import AppSettings, SettingsRepository


def ts(text: str):
    """Shorthand for an aware datetime in tests."""
    return parse_timestamp(text)


# ----- repositories --------------------------------------------------------


class InMemoryLinkRepository(LinkRepository):
    def __init__(self):
        self.items: dict[str, Link] = {}

    def list_all(self):
        return list(self.items.values())

    def get(self, link_id):
        return self.items.get(link_id)

    def save(self, link):
        self.items[link.id] = link

    def delete(self, link_id):
        return self.items.pop(link_id, None) is not None


class InMemoryFilterRepository(LogFilterRepository):
    def __init__(self, items: list[LogFilter] | None = None):
        self.items = {item.id: item for item in items or []}

    def list_all(self):
        return list(self.items.values())

    def get(self, filter_id):
        return self.items.get(filter_id)

    def save(self, log_filter):
        self.items[log_filter.id] = log_filter

    def delete(self, filter_id):
        return self.items.pop(filter_id, None) is not None


class FakeCommandRunner(CommandRunner):
    def __init__(self, error: Exception | None = None):
        self.ran: list[str] = []
        self.error = error

    def run(self, command):
        self.ran.append(command)
        if self.error:
            raise self.error
        return "Program started"


class FakeLogProvider(LogProvider):
    def __init__(self, entries: list[LogEntry] | None = None, error: Exception | None = None,
                 available: bool = True):
        self.entries = entries if entries is not None else [
            LogEntry(timestamp="2026-01-01T00:00:00Z", severity="ERROR", summary="boom")
        ]
        self.error = error
        self.available = available
        self.last_query: LogQuery | None = None

    def is_available(self):
        return self.available

    def query(self, query):
        self.last_query = query
        if self.error:
            raise self.error
        return list(self.entries)

    def describe(self, query):
        return f"fake --project={query.project}"


class InMemorySettingsRepository(SettingsRepository):
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()

    def load(self):
        return AppSettings(**vars(self.settings))

    def save(self, settings):
        self.settings = AppSettings(**vars(settings))


class FakeImageStore(ImageStore):
    def __init__(self):
        self.saved: list[tuple[bytes, str]] = []

    def save(self, content, extension):
        self.saved.append((content, extension))
        return f"/images/{len(self.saved)}.{extension}"


# ----- GitHub --------------------------------------------------------------


def make_event(id_, kind="push", repo="acme/core", actor="bob", at="2026-10-22T10:00:00Z", **extra):
    values = dict(id=str(id_), kind=EventKind(kind), type="", repo=repo, actor=actor,
                  created_at=parse_timestamp(at), summary=kind)
    values.update(extra)
    return GithubEvent(**values)


def make_push(id_, at, actor="bob", ref="main", before="b", head="h", repo="acme/core"):
    return make_event(id_, "push", repo=repo, actor=actor, at=at, ref=ref, type="PushEvent",
                  details=PushDetails(before=before, head=head, heads=(head,)))


def make_code_comment(id_, at, number=13, actor="bob"):
    return make_event(id_, "comment", actor=actor, at=at, number=number,
                  type="PullRequestReviewCommentEvent", url=f"https://x/{id_}", body=f"c{id_}",
                  is_pull=True, details=CodeCommentDetails(path=f"f{id_}.py"))


def make_batch(times, truncated=True):
    return GithubEventBatch([make_event(i, at=at) for i, at in enumerate(times)], truncated)


def make_pr_action(id_, action, actor="carol", at="2026-10-23T12:12:17Z", number=112, source=""):
    return make_event(id_, "pr", type="PullRequestEvent", action=action, actor=actor, at=at,
                      number=number, repo="acme/dockerfiles", is_pull=True, details=PullDetails(),
                      source=EventSource(source) if source else EventSource.EVENTS)


def make_review(id_, state, actor="carol", at="2026-10-23T12:12:06Z", source=""):
    return make_event(id_, "review", type="PullRequestReviewEvent", action="created", actor=actor,
                      at=at, number=112, repo="acme/dockerfiles", is_pull=True,
                      details=ReviewDetails(state=ReviewState(state)),
                      source=EventSource(source) if source else EventSource.EVENTS)


class InMemoryFeedRepository(GithubFeedRepository):
    def __init__(self):
        self.items: dict[str, GithubFeed] = {}

    def list_all(self):
        return list(self.items.values())

    def get(self, feed_id):
        return self.items.get(feed_id)

    def save(self, feed):
        self.items[feed.id] = feed

    def delete(self, feed_id):
        return self.items.pop(feed_id, None) is not None


class FakeGithubProvider(GithubProvider):
    """Builds snapshots from plain dicts. Mirrors the adapter's policy for a
    repo that fails (a warning, unless every repo failed) so service tests
    can exercise the warnings path."""

    def __init__(self):
        self.org = {}  # org -> events
        self.repos = {}  # (org, repo) -> events | Exception
        self.truncated = set()  # org names / (org, repo) cut by the page limit
        self.searched = []  # near-real-time PR events, or an Exception
        self.activity_calls = []  # (spec, realtime_since)
        self.quota = None
        self.prs = {}
        self.pr_calls = []

    def activity(self, spec, realtime_since):
        self.activity_calls.append((spec, realtime_since))
        warnings = []
        if not spec.repos:
            events = [GithubEvent(**vars(e)) for e in self.org.get(spec.org, [])]
            batches = [GithubEventBatch(events, spec.org in self.truncated)]
        else:
            batches, errors = [], []
            for repo in spec.repos:
                value = self.repos.get((spec.org, repo), [])
                if isinstance(value, GithubNotFoundError):
                    errors.append(f"{spec.org}/{repo} does not exist or the token cannot see it")
                    value = []
                elif isinstance(value, Exception):
                    errors.append(value)
                    value = []
                batches.append(GithubEventBatch([GithubEvent(**vars(e)) for e in value],
                                                (spec.org, repo) in self.truncated))
            if errors and len(errors) == len(spec.repos):
                first = errors[0]
                raise first if isinstance(first, Exception) else GithubNotFoundError(first)
            warnings += [str(error) for error in errors]
        if isinstance(self.searched, Exception):
            warnings.append(f"Real-time PR search unavailable: {self.searched}")
            realtime = []
        else:
            realtime = [GithubEvent(**vars(e)) for e in self.searched]
        return ActivitySnapshot(viewer="alice", batches=batches, realtime=realtime,
                                warnings=warnings, quota=self.quota)

    def org_repos(self, org):
        return []

    def pull_requests(self, refs):
        self.pr_calls.append(list(refs))
        return {ref: self.prs[ref] for ref in refs if ref in self.prs}
