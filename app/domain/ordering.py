"""Manual ordering of cards, filters and feeds: anything with a position,
a creation time and an id (ties break on creation, then id)."""

from datetime import datetime
from typing import Protocol


class Orderable(Protocol):
    id: str
    position: int
    created_at: datetime


def ordered[O: Orderable](items: list[O]) -> list[O]:
    return sorted(items, key=lambda item: (item.position, item.created_at, item.id))


def next_position(items: list[Orderable]) -> int:
    return max((item.position for item in items), default=-1) + 1
