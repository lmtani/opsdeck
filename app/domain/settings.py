"""Settings context: user preferences persisted across sessions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AppSettings:
    theme: str = "midnight"
    polling: bool = True  # the live switch: background counts, badges, notifications and auto refresh


class SettingsRepository(ABC):
    @abstractmethod
    def load(self) -> AppSettings: ...

    @abstractmethod
    def save(self, settings: AppSettings) -> None: ...
