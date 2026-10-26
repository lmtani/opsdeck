"""Links context: shortcut cards (a URL to open or a local command to run)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, utc_now
from app.domain.errors import ValidationError
from app.domain.repository import Repository


class LinkKind(StrEnum):
    URL = "url"  # opens in the browser
    COMMAND = "command"  # runs a program on the user's machine


def _kind(value) -> LinkKind:
    try:
        return LinkKind(value)
    except ValueError:
        raise ValidationError("Invalid card type") from None


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _fields(title, url, kind, command, description, image_url, category, accent) -> dict:
    """The editable fields, trimmed, as `new` and `update` take them."""
    return dict(
        title=_clean(title),
        url=_clean(url),
        kind=_kind(kind),
        command=_clean(command),
        description=_clean(description),
        image_url=_clean(image_url),
        category=_clean(category),
        accent=_clean(accent),
    )


@dataclass
class Link:
    """A shortcut card on the dashboard (e.g. a GCP project console URL).

    Invariants, checked on every construction: a title, and a URL for URL
    cards or a command line for command cards. Build new ones with `new`
    and edit with `update`, which also trim the text fields.
    """

    id: str
    title: str
    url: str = ""
    kind: LinkKind = LinkKind.URL
    command: str = ""  # shell command line, used only when kind == COMMAND
    description: str = ""
    image_url: str = ""
    category: str = ""
    accent: str = ""
    position: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        self.kind = _kind(self.kind)
        if not self.title.strip():
            raise ValidationError("Title is required")
        if self.kind is LinkKind.URL and not self.url.strip():
            raise ValidationError("URL is required")
        if self.kind is LinkKind.COMMAND and not self.command.strip():
            raise ValidationError("Command is required")

    @classmethod
    def new(
        cls,
        title: str,
        url: str = "",
        kind: LinkKind | str = LinkKind.URL,
        command: str = "",
        description: str = "",
        image_url: str = "",
        category: str = "",
        accent: str = "",
        position: int = 0,
    ) -> "Link":
        values = _fields(title, url, kind, command, description, image_url, category, accent)
        return cls(id=new_id(), position=position, **values)

    def update(
        self,
        title: str,
        url: str = "",
        kind: LinkKind | str = LinkKind.URL,
        command: str = "",
        description: str = "",
        image_url: str = "",
        category: str = "",
        accent: str = "",
    ) -> None:
        """Replace the editable fields; id, position and created_at stay."""
        values = _fields(title, url, kind, command, description, image_url, category, accent)
        replace(self, **values)  # validates the result before anything changes
        for name, value in values.items():
            setattr(self, name, value)


class LinkRepository(Repository[Link]):
    """Cards, by id."""


class CommandRunner(ABC):
    """Launches a local command (e.g. opening a desktop program)."""

    @abstractmethod
    def run(self, command: str) -> str:
        """Start the command, returning a short human-readable status."""


class ImageStore(ABC):
    """Persists uploaded card images and returns their public URL path."""

    @abstractmethod
    def save(self, content: bytes, extension: str) -> str: ...
