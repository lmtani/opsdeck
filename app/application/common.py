"""Persisting a manual order through any repository of orderable entities."""

from app.domain.ordering import Orderable
from app.domain.repository import Repository


def apply_order[O: Orderable](repository: Repository[O], ids: list[str]) -> None:
    """Persist the given id sequence as positions 0..n-1 (unknown ids ignored)."""
    by_id = {item.id: item for item in repository.list_all()}
    position = 0
    for item_id in ids:
        item = by_id.get(item_id)
        if item is None:
            continue
        if item.position != position:
            item.position = position
            repository.save(item)
        position += 1
