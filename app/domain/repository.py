"""Generic persistence port: the same contract for cards, filters and feeds."""

from abc import ABC, abstractmethod


class Repository[T](ABC):
    @abstractmethod
    def list_all(self) -> list[T]: ...

    @abstractmethod
    def get(self, entity_id: str) -> T | None: ...

    @abstractmethod
    def save(self, entity: T) -> None:
        """Insert or update by id."""

    @abstractmethod
    def delete(self, entity_id: str) -> bool:
        """Return True if something was deleted."""
