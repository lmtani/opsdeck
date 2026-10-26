"""Row <-> entity mapping shared by the MySQL and JSON repositories.

Rows hold only plain values (str, int, bool, list of str): enums become
their values and datetimes ISO-8601 UTC text on the way out, and both are
rebuilt on the way in. The feed's lists stay lists here; the MySQL
repository joins them with commas itself.
"""

from dataclasses import asdict

from app.domain.common import EPOCH, format_timestamp, parse_timestamp
from app.domain.github import EventKind, GithubFeed
from app.domain.links import Link, LinkKind
from app.domain.logs import Freshness, LogFilter


def _text(value) -> str:
    return format_timestamp(value) if value else ""


def _created_at(row: dict):
    # rows written before validation existed could hold ''; keep them sortable
    return parse_timestamp(row.get("created_at")) or EPOCH


def link_to_row(link: Link) -> dict:
    row = asdict(link)
    row["kind"] = link.kind.value
    row["created_at"] = _text(link.created_at)
    return row


def row_to_link(row: dict) -> Link:
    values = dict(row)
    values["kind"] = LinkKind(values.get("kind") or LinkKind.URL)
    values["created_at"] = _created_at(row)
    return Link(**values)


def filter_to_row(log_filter: LogFilter) -> dict:
    row = asdict(log_filter)
    row["freshness"] = log_filter.freshness.value
    row["created_at"] = _text(log_filter.created_at)
    return row


def row_to_filter(row: dict) -> LogFilter:
    values = dict(row)
    values["freshness"] = Freshness(values.get("freshness") or Freshness.H1)
    values["created_at"] = _created_at(row)
    return LogFilter(**values)


def feed_to_row(feed: GithubFeed) -> dict:
    row = asdict(feed)
    row["event_kinds"] = [kind.value for kind in feed.event_kinds]
    row["hide_own"] = bool(feed.hide_own)
    row["hide_bots"] = bool(feed.hide_bots)
    row["last_seen_at"] = _text(feed.last_seen_at)
    row["created_at"] = _text(feed.created_at)
    return row


def row_to_feed(row: dict) -> GithubFeed:
    values = dict(row)
    values["repos"] = list(values.get("repos") or [])
    values["event_kinds"] = [EventKind(kind) for kind in values.get("event_kinds") or []]
    # MySQL hands TINYINT(1) back as int
    values["hide_own"] = bool(values.get("hide_own"))
    values["hide_bots"] = bool(values.get("hide_bots"))
    values["last_seen_at"] = parse_timestamp(values.get("last_seen_at"))
    values["created_at"] = _created_at(row)
    return GithubFeed(**values)
