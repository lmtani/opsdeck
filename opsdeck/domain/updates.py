"""Updates context: which OpsDeck release is the newest, and whether the one
running is behind it."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """`v1.2.3` or `1.2.3` as a comparable tuple; None for anything else
    (a pre-release or local suffix such as `1.2.3rc1` or `1.2.3+abc` too)."""
    match = _VERSION.fullmatch(text.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


@dataclass(frozen=True)
class Release:
    version: str  # without the leading "v"
    url: str  # the release page, with its notes
    published_at: datetime | None = None

    def is_newer_than(self, current: str) -> bool:
        mine, theirs = parse_version(current), parse_version(self.version)
        return mine is not None and theirs is not None and theirs > mine


class ReleaseFeed(ABC):
    """Port: where the releases are published."""

    @abstractmethod
    def latest(self) -> Release | None:
        """The newest published release; None when there is none yet. Raises
        ExternalServiceError when the feed cannot be read."""
