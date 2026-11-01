"""Settings context: user preferences persisted across sessions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from opsdeck.domain.errors import ValidationError

# How often each background round may run, in seconds. Fixed choices keep the
# load on the backends bounded: a watched filter is read at most once a minute
# anyway (LogWatchService), Cloud Logging allows 60 reads per minute per
# project and GitHub's search budget is spent by every feed refresh.
EVERY_CHOICES: dict[str, tuple[int, ...]] = {
    "logs_every": (60, 120, 300, 600, 900),  # count of watched log filters
    "github_every": (60, 120, 300, 600, 900),  # feed badges and notifications
    "feed_refresh_every": (30, 60, 120, 300),  # auto refresh of the open feed
}


@dataclass
class AppSettings:
    theme: str = "midnight"
    polling: bool = True  # the live switch: background counts, badges, notifications and auto refresh
    logs_every: int = 120
    github_every: int = 300
    feed_refresh_every: int = 60

    def __post_init__(self):
        for name, choices in EVERY_CHOICES.items():
            if getattr(self, name) not in choices:
                allowed = ", ".join(str(choice) for choice in choices)
                raise ValidationError(f"{name} must be one of {allowed} seconds")


class SettingsRepository(ABC):
    @abstractmethod
    def load(self) -> AppSettings: ...

    @abstractmethod
    def save(self, settings: AppSettings) -> None: ...
