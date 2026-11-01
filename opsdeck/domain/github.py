"""GitHub context: saved activity feeds over an org and the events they show.

The pure rules (feed validation, burst folding, coverage, real-time merge)
live in github_rules.py so this module stays entities and ports only.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from opsdeck.domain.common import new_id, utc_now
from opsdeck.domain.errors import ValidationError
from opsdeck.domain.repository import Repository

GITHUB_ORG_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
GITHUB_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
MAX_FEED_REPOS = 40


class EventKind(StrEnum):
    """Event categories a feed can be narrowed to (GithubFeed.event_kinds)."""

    PUSH = "push"
    PR = "pr"
    REVIEW = "review"
    COMMENT = "comment"
    ISSUE = "issue"
    BRANCH = "branch"
    RELEASE = "release"
    OTHER = "other"


class ItemState(StrEnum):
    """State of the PR or issue an event is about, when known."""

    OPEN = "open"
    DRAFT = "draft"
    MERGED = "merged"
    CLOSED = "closed"


def validate_org(org: str) -> str:
    org = (org or "").strip()
    if not GITHUB_ORG_NAME.match(org):
        raise ValidationError("Invalid GitHub organization")
    return org


def normalize_repos(org: str, repos) -> tuple[str, ...]:
    """Accept bare names, "org/name" or GitHub URLs; dedupe case-insensitively."""
    result: list[str] = []
    for raw in repos:
        text = (raw or "").strip().rstrip("/")
        if not text:
            continue
        if "github.com/" in text:
            text = text.split("github.com/", 1)[1]
        if "/" in text:
            owner, _, rest = text.partition("/")
            if owner.lower() != org.lower():
                raise ValidationError(f"Repository '{raw}' does not belong to org {org}")
            text = rest.split("/", 1)[0]
        text = text.removesuffix(".git")
        if not GITHUB_REPO_NAME.match(text):
            raise ValidationError(f"Invalid repository name: '{raw}'")
        if text.lower() not in {name.lower() for name in result}:
            result.append(text)
    if len(result) > MAX_FEED_REPOS:
        raise ValidationError(f"Use at most {MAX_FEED_REPOS} repositories per feed")
    return tuple(result)


def normalize_kinds(kinds) -> tuple[EventKind, ...]:
    """Canonical order, unknown names rejected; every kind == no filter."""
    known = {kind.value for kind in EventKind}
    unknown = [str(kind) for kind in kinds if kind not in known]
    if unknown:
        raise ValidationError(f"Invalid event kind: {', '.join(unknown)}")
    selected = tuple(kind for kind in EventKind if kind in kinds)
    return () if len(selected) == len(EventKind) else selected


@dataclass(frozen=True)
class FeedSpec:
    """What a feed reads: one org, optionally narrowed to repos and kinds.

    Normalized on construction (org validated, repo names canonical and
    deduplicated, kinds in canonical order), so an instance is always valid.
    An ad-hoc query is a spec; a saved feed carries one.
    """

    org: str
    repos: tuple[str, ...] = ()  # empty = whole org
    event_kinds: tuple[EventKind, ...] = ()  # empty = all
    hide_own: bool = False  # drop events whose actor is the authenticated user
    hide_bots: bool = True  # drop events from "*[bot]" actors

    def __post_init__(self):
        org = validate_org(self.org)
        object.__setattr__(self, "org", org)
        object.__setattr__(self, "repos", normalize_repos(org, self.repos))
        object.__setattr__(self, "event_kinds", normalize_kinds(self.event_kinds))


class ReviewState(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"
    DISMISSED = "dismissed"


class EventSource(StrEnum):
    EVENTS = "events"  # the events API: canonical, but published with a delay
    SEARCH = "search"  # PR search: near real time, replaced once the API publishes


class SubjectType(StrEnum):
    """The GitHub object an event is about, which holds its full text."""

    PULL = "pull"
    ISSUE = "issue"
    ISSUE_COMMENT = "issue_comment"  # also comments on a PR's conversation
    REVIEW = "review"
    REVIEW_COMMENT = "review_comment"  # a comment on a line of a PR's diff
    RELEASE = "release"
    COMMIT_COMMENT = "commit_comment"


# which identifiers each subject needs to be fetched
_SUBJECT_NEEDS = {
    SubjectType.PULL: ("number",),
    SubjectType.ISSUE: ("number",),
    SubjectType.REVIEW: ("number", "item_id"),
    SubjectType.ISSUE_COMMENT: ("item_id",),
    SubjectType.REVIEW_COMMENT: ("item_id",),
    SubjectType.RELEASE: ("item_id",),
    SubjectType.COMMIT_COMMENT: ("item_id",),
}


@dataclass(frozen=True)
class EventSubject:
    """Enough to fetch what an event is about: identifiers only, so the
    adapter builds every URL itself."""

    type: SubjectType
    repo: str  # "org/name"
    number: int = 0  # PR or issue number
    item_id: int = 0  # comment, review or release id

    def __post_init__(self):
        object.__setattr__(self, "type", SubjectType(self.type))
        owner, _, name = (self.repo or "").partition("/")
        if not GITHUB_ORG_NAME.match(owner) or not GITHUB_REPO_NAME.match(name) or name in (".", ".."):
            raise ValidationError("Invalid GitHub repository")
        for needed in _SUBJECT_NEEDS[self.type]:
            value = getattr(self, needed)
            if not isinstance(value, int) or value <= 0:
                raise ValidationError(f"A {self.type} subject needs a positive {needed}")


# ----- event details: one value object per shape, immutable --------------


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str


@dataclass(frozen=True)
class PushDetails:
    before: str = ""
    head: str = ""
    heads: tuple[str, ...] = ()  # newest first; grows when consecutive pushes are folded
    commits: tuple[Commit, ...] = ()  # only in the fuller, older payloads


@dataclass(frozen=True)
class PullDetails:
    head_ref: str = ""
    base_ref: str = ""


@dataclass(frozen=True)
class ReviewDetails:
    state: ReviewState | None = None


@dataclass(frozen=True)
class CodeComment:
    url: str
    path: str
    body: str
    subject: EventSubject | None = None


@dataclass(frozen=True)
class CodeCommentDetails:
    path: str = ""
    line: int | None = None
    comments: tuple[CodeComment, ...] = ()  # every comment of a folded burst


@dataclass(frozen=True)
class RefDetails:
    ref_type: str = ""  # branch, tag or repository


EventDetails = PushDetails | PullDetails | ReviewDetails | CodeCommentDetails | RefDetails | None


def _clean(text: str | None) -> str:
    return (text or "").strip()


@dataclass
class GithubFeed:
    """A saved, named FeedSpec plus the user's reading position.

    Invariants: a name and a valid spec (see FeedSpec); the spec fields are
    normalized on construction. Build with `new`, edit with `update`.
    """

    id: str
    name: str
    org: str
    repos: list[str] = field(default_factory=list)  # repo names inside org; empty = whole org
    event_kinds: list[EventKind] = field(default_factory=list)  # empty = all
    hide_own: bool = False
    hide_bots: bool = True
    description: str = ""
    last_seen_at: datetime | None = None  # newest event the user has already seen
    position: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        spec = self.spec  # validates and normalizes
        self.org, self.repos, self.event_kinds = spec.org, list(spec.repos), list(spec.event_kinds)
        if not self.name.strip():
            raise ValidationError("Name is required")

    @property
    def spec(self) -> FeedSpec:
        return FeedSpec(
            org=self.org,
            repos=tuple(self.repos),
            event_kinds=tuple(self.event_kinds),
            hide_own=self.hide_own,
            hide_bots=self.hide_bots,
        )

    @classmethod
    def new(
        cls,
        name: str,
        org: str,
        repos=None,
        event_kinds=None,
        hide_own: bool = False,
        hide_bots: bool = True,
        description: str = "",
        position: int = 0,
    ) -> "GithubFeed":
        return cls(
            id=new_id(),
            name=_clean(name),
            org=org,
            repos=list(repos or []),
            event_kinds=list(event_kinds or []),
            hide_own=hide_own,
            hide_bots=hide_bots,
            description=_clean(description),
            # start "caught up": only what happens after the feed exists is new
            last_seen_at=utc_now(),
            position=position,
        )

    def update(
        self,
        name: str,
        org: str,
        repos=None,
        event_kinds=None,
        hide_own: bool = False,
        hide_bots: bool = True,
        description: str = "",
    ) -> None:
        """Replace the editable fields; id, position, mark and created_at stay."""
        values: dict[str, Any] = dict(
            name=_clean(name),
            org=org,
            repos=list(repos or []),
            event_kinds=list(event_kinds or []),
            hide_own=hide_own,
            hide_bots=hide_bots,
            description=_clean(description),
        )
        candidate = replace(self, **values)  # validates and normalizes
        for field_name in values:
            setattr(self, field_name, getattr(candidate, field_name))

    def mark_seen(self, at: datetime | None = None) -> bool:
        """Move the last-seen mark forward (never back) to `at` or now."""
        mark = at or utc_now()
        if self.last_seen_at is not None and mark <= self.last_seen_at:
            return False
        self.last_seen_at = mark
        return True


@dataclass
class GithubEvent:
    """A normalized GitHub activity event, ready for display."""

    id: str
    kind: EventKind
    type: str  # raw GitHub type, e.g. "PullRequestEvent"
    repo: str  # "org/name"
    actor: str
    created_at: datetime
    summary: str  # what the actor did, in Portuguese, without the actor name
    url: str = ""
    actor_avatar: str = ""
    action: str = ""
    ref: str = ""  # branch or tag
    number: int = 0  # PR / issue number
    title: str = ""
    state: ItemState | None = None
    body: str = ""  # excerpt of the comment / review / issue text; the full text is in the context
    is_pull: bool = False  # `number` refers to a pull request (else an issue, or nothing)
    source: EventSource = EventSource.EVENTS
    details: EventDetails = None
    count: int = 1  # > 1 when consecutive pushes were collapsed into this one
    is_new: bool = False  # newer than the feed's last-seen mark (set for saved feeds)
    subject: EventSubject | None = None  # what to fetch for the full context, when there is any


@dataclass(frozen=True)
class ThreadComment:
    """One comment around an event: a reply in a review thread, or a line
    comment of a review."""

    id: int
    author: str
    author_avatar: str = ""
    created_at: datetime | None = None
    body_html: str = ""  # sanitized, like EventContext.body_html
    url: str = ""
    path: str = ""
    line: int | None = None
    diff_hunk: str = ""


@dataclass(frozen=True)
class Label:
    name: str
    color: str = ""  # six hex digits, as GitHub sends it; empty when not valid


@dataclass(frozen=True)
class Reviewer:
    login: str  # a user, or "org/team" for a requested team
    state: ReviewState | None = None  # their latest review; None = requested, not reviewed yet


@dataclass(frozen=True)
class PullStats:
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    commits: int = 0
    head_ref: str = ""
    base_ref: str = ""
    labels: tuple[Label, ...] = ()
    reviewers: tuple[Reviewer, ...] = ()


@dataclass(frozen=True)
class EventContext:
    """The full text behind an event, fetched when the user opens it, with
    the conversation around it: the whole thread of a review comment (oldest
    first, the event's own comment included) or the line comments of a
    review; and, for a pull request, its size, labels and reviewers."""

    subject: EventSubject
    url: str = ""
    author: str = ""
    author_avatar: str = ""
    created_at: datetime | None = None
    title: str = ""  # of the PR, issue or release
    state: ItemState | None = None
    review_state: ReviewState | None = None
    body_html: str = ""  # rendered by GitHub and sanitized by the adapter: safe to display
    path: str = ""  # file of a code comment
    line: int | None = None
    diff_hunk: str = ""  # the diff lines a code comment refers to, ending at its line
    thread: tuple[ThreadComment, ...] = ()
    pull: PullStats | None = None


@dataclass
class GithubEventBatch:
    """Events read from one source (the org or one repo), newest first.

    `truncated` means the source still had older events when the read hit
    its page limit: history before the batch is missing, not quiet.
    """

    events: list[GithubEvent]
    truncated: bool = False


@dataclass
class GithubRepo:
    name: str
    full_name: str
    private: bool = False
    archived: bool = False
    description: str = ""
    pushed_at: datetime | None = None
    url: str = ""


@dataclass
class PullRequestInfo:
    title: str
    state: ItemState | None
    url: str = ""


class TokenKind(StrEnum):
    NONE = "none"  # no token configured
    CLASSIC = "classic"  # personal access token (classic), ghp_
    FINE_GRAINED = "fine-grained"  # fine-grained personal access token, github_pat_
    OAUTH = "oauth"  # OAuth token, e.g. from `gh auth token`, gho_
    OTHER = "other"


@dataclass
class TokenStatus:
    """What the configured GitHub token can do, in words the user can act on.

    `problems` are reasons the GitHub tab will not work fully; `notes` are
    advice that cannot be checked up front (a fine-grained token's grants
    only show when a feed is read).
    """

    kind: TokenKind
    login: str = ""
    scopes: tuple[str, ...] | None = None  # classic and OAuth tokens only
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.kind is not TokenKind.NONE and not self.problems


@dataclass(frozen=True)
class Quota:
    """What is left of the provider's request allowance."""

    remaining: int
    limit: int


@dataclass
class ActivitySnapshot:
    """One read of a feed spec from the provider, before the timeline rules run.

    `batches` are the published events of each source: the whole org, or one
    batch per repo. `realtime` is recent PR activity from a near-real-time
    source, which may be unavailable (then it is empty and `warnings` says
    why). A source that could not be read becomes a warning rather than a
    failure, unless no source could be read at all.
    """

    viewer: str  # login the provider acts as; its own actions are "mine"
    batches: list[GithubEventBatch]
    realtime: list[GithubEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quota: Quota | None = None


@dataclass
class GithubActivity:
    """Result of reading a feed: events plus what the UI needs to describe them."""

    events: list[GithubEvent]
    viewer: str = ""
    fetched_at: datetime = field(default_factory=utc_now)
    warnings: list[str] = field(default_factory=list)
    # before this instant the timeline is incomplete (a source was cut by the
    # API's event cap); None when every source was read to its end
    coverage_start: datetime | None = None
    quota: Quota | None = None
    unseen: int = 0  # events flagged is_new (saved feeds only)


class ContributionKind(StrEnum):
    """What counts as a contribution, as on GitHub's contribution graph."""

    COMMIT = "commit"  # a commit on the default branch (merge commits excluded)
    PR = "pr"  # a pull request opened
    REVIEW = "review"  # a review of someone else's pull request
    ISSUE = "issue"  # an issue opened


@dataclass(frozen=True)
class Contribution:
    login: str
    kind: ContributionKind
    repo: str  # "org/name"
    at: datetime
    avatar: str = ""
    number: int = 0  # the PR or issue (also the PR a review is about)
    title: str = ""  # commit headline, or the PR / issue title
    url: str = ""  # the commit, PR, issue or review on github.com
    review_state: ReviewState | None = None


@dataclass
class ContributionsSnapshot:
    """One read of a feed spec's contributions since an instant, before the
    rules bucket them by day."""

    viewer: str
    contributions: list[Contribution]
    warnings: list[str] = field(default_factory=list)
    quota: Quota | None = None
    fetched_at: datetime = field(default_factory=utc_now)  # a cached snapshot keeps its read time


@dataclass(frozen=True)
class DailyContribution:
    """How many contributions of one kind a person made to one repo on one
    (local) day."""

    login: str
    day: date
    kind: ContributionKind
    repo: str
    count: int


@dataclass
class GithubContributions:
    """Daily contributions per person over [since, until], local days."""

    since: date
    until: date
    counts: list[DailyContribution]
    avatars: dict[str, str] = field(default_factory=dict)  # login -> avatar URL
    viewer: str = ""
    fetched_at: datetime = field(default_factory=utc_now)
    warnings: list[str] = field(default_factory=list)
    quota: Quota | None = None


class GithubFeedRepository(Repository[GithubFeed]):
    """Saved feeds, by id."""


class GithubProvider(ABC):
    """Reads a GitHub org's activity on behalf of the configured user."""

    def is_available(self) -> bool:
        """Whether credentials are configured (health check, no network)."""
        return True

    def token_status(self) -> TokenStatus:
        """Check the credentials against GitHub and explain what is missing."""
        return TokenStatus(kind=TokenKind.OTHER if self.is_available() else TokenKind.NONE)

    @abstractmethod
    def activity(self, spec: FeedSpec, realtime_since: datetime) -> ActivitySnapshot:
        """Everything one timeline read needs for the spec's org or repos:
        the published events of each source, PR activity since
        `realtime_since` from a near-real-time source, and the quota left."""

    @abstractmethod
    def org_repos(self, org: str) -> list[GithubRepo]:
        """Every repo of the org visible to the user, most recently pushed first."""

    @abstractmethod
    def pull_requests(self, refs: list[tuple[str, int]]) -> dict[tuple[str, int], PullRequestInfo]:
        """Current title and state of PRs given as ("org/repo", number);
        unknown refs are omitted."""

    @abstractmethod
    def contributions(self, spec: FeedSpec, since: datetime) -> ContributionsSnapshot:
        """Contributions made since `since` to the spec's repos (or to the
        org's repos pushed since then): commits on each default branch, PRs
        and issues opened, reviews submitted. A repo that cannot be read
        becomes a warning."""

    @abstractmethod
    def event_context(self, subject: EventSubject) -> EventContext:
        """The full, rendered text of what an event is about. Raises
        GithubNotFoundError when it was deleted or the token cannot see it."""
