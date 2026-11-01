"""Row <-> entity mapping for the SQL repositories.

Rows hold only plain values (str, int, bool, list of str): enums become
their values and datetimes ISO-8601 UTC text on the way out, and both are
rebuilt on the way in. The feed's lists and a source's config stay a list
and a dict here; the SQL repository turns them into text itself. Settings
are name/value text rows, so their booleans and numbers are parsed back.
"""

from dataclasses import asdict, fields

from opsdeck.domain.common import EPOCH, format_timestamp, parse_timestamp
from opsdeck.domain.github import EventKind, GithubFeed
from opsdeck.domain.links import Link
from opsdeck.domain.logs import Freshness, LogFilter, LogSource, LogSourceKind
from opsdeck.domain.settings import AppSettings


def _text(value) -> str:
    return format_timestamp(value) if value else ""


def _created_at(row: dict):
    # rows written before validation existed could hold ''; keep them sortable
    return parse_timestamp(row.get("created_at")) or EPOCH


def link_to_row(link: Link) -> dict:
    row = asdict(link)
    row["created_at"] = _text(link.created_at)
    return row


def row_to_link(row: dict) -> Link:
    values = dict(row)
    values["created_at"] = _created_at(row)
    return Link(**values)


def filter_to_row(log_filter: LogFilter) -> dict:
    row = asdict(log_filter)
    row["freshness"] = log_filter.freshness.value
    row["watch"] = bool(log_filter.watch)
    row["last_seen_at"] = _text(log_filter.last_seen_at)
    row["created_at"] = _text(log_filter.created_at)
    return row


def row_to_filter(row: dict) -> LogFilter:
    values = dict(row)
    values["freshness"] = Freshness(values.get("freshness") or Freshness.H1)
    values["watch"] = bool(values.get("watch"))  # TINYINT(1) comes back as int
    values["last_seen_at"] = parse_timestamp(values.get("last_seen_at"))
    values["created_at"] = _created_at(row)
    return LogFilter(**values)


def source_to_row(source: LogSource) -> dict:
    row = asdict(source)
    row["kind"] = source.kind.value
    row["created_at"] = _text(source.created_at)
    return row


def row_to_source(row: dict) -> LogSource:
    values = dict(row)
    values["kind"] = LogSourceKind(values["kind"])
    values["config"] = dict(values.get("config") or {})
    values["created_at"] = _created_at(row)
    return LogSource(**values)


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


def values_to_settings(values: dict) -> AppSettings:
    """Unknown names are ignored (a preference that no longer exists) and
    missing ones keep their default."""
    known = {}
    for field in fields(AppSettings):
        if field.name not in values:
            continue
        value = values[field.name]
        if isinstance(field.default, bool):
            value = str(value).strip().lower() in ("1", "true")
        elif isinstance(field.default, int):  # stored as text
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        known[field.name] = value
    return AppSettings(**known)
