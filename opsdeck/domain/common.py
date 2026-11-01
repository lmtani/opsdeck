"""Ids and time, shared by every context. No I/O."""

import uuid
from datetime import UTC, datetime

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_timestamp(value) -> datetime | None:
    """ISO-8601 text (or a datetime) to an aware datetime; None when invalid.

    Naive values are taken as UTC. Both "...Z" and "...+00:00" are accepted,
    which is what GitHub, the stored rows and the browser send.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_timestamp(value: datetime) -> str:
    """Aware datetime to ISO-8601 UTC text ending in Z (stored rows, logs)."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
