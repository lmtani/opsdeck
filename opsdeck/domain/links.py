"""Links context: shortcut cards, each a URL to open."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from opsdeck.domain.common import new_id, utc_now
from opsdeck.domain.errors import ValidationError
from opsdeck.domain.repository import Repository

COMMAND_CARDS_REMOVED = "Command cards were removed from OpsDeck"


def is_command_card(raw: Any) -> bool:
    """Whether a card from an older backup or export is a command card, a kind
    OpsDeck no longer has: it launched programs on the machine running it."""
    return isinstance(raw, dict) and raw.get("kind") == "command"


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _fields(title, url, description, image_url, category, accent) -> dict:
    """The editable fields, trimmed, as `new` and `update` take them."""
    return dict(
        title=_clean(title),
        url=_clean(url),
        description=_clean(description),
        image_url=_clean(image_url),
        category=_clean(category),
        accent=_clean(accent),
    )


@dataclass
class Link:
    """A shortcut card on the dashboard (e.g. a GCP project console URL).

    Invariants, checked on every construction: a title and a URL. Build new
    ones with `new` and edit with `update`, which also trim the text fields.
    """

    id: str
    title: str
    url: str = ""
    description: str = ""
    image_url: str = ""
    category: str = ""
    accent: str = ""
    position: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        if not self.title.strip():
            raise ValidationError("Title is required")
        if not self.url.strip():
            raise ValidationError("URL is required")

    @classmethod
    def new(
        cls,
        title: str,
        url: str = "",
        description: str = "",
        image_url: str = "",
        category: str = "",
        accent: str = "",
        position: int = 0,
    ) -> "Link":
        values = _fields(title, url, description, image_url, category, accent)
        return cls(id=new_id(), position=position, **values)

    def update(
        self,
        title: str,
        url: str = "",
        description: str = "",
        image_url: str = "",
        category: str = "",
        accent: str = "",
    ) -> None:
        """Replace the editable fields; id, position and created_at stay."""
        values = _fields(title, url, description, image_url, category, accent)
        replace(self, **values)  # validates the result before anything changes
        for name, value in values.items():
            setattr(self, name, value)


class LinkRepository(Repository[Link]):
    """Cards, by id."""


class ImageStore(ABC):
    """Persists uploaded card images and returns their public URL path."""

    @abstractmethod
    def save(self, content: bytes, extension: str) -> str: ...

    @abstractmethod
    def load(self, url: str) -> tuple[bytes, str] | None:
        """Content and extension of an image this store saved, by its URL path;
        None for anything else (an external URL, a missing file)."""
