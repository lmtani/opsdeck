"""Use case of backups: everything an OpsDeck holds, in one JSON document
that restores it on this or another installation.

Unlike a shared bundle (sharing.py), a backup is complete and exact: every
card, log source, filter, feed and the settings, with their ids, order,
creation times and reading marks (watched filters, feed marks). Uploaded card
images travel inside it as data URIs. A restore replaces what is there: it
builds and checks every entity first, writes the backup's items over the
current ones and only then removes what the backup does not have, so a
failure halfway leaves a mix rather than an empty dashboard.
"""

import types
import typing
from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Any

from app.application.links import ImageService
from app.application.sharing import decode_image_uri, encode_image_uri
from app.domain.common import format_timestamp, parse_timestamp, utc_now
from app.domain.errors import ValidationError
from app.domain.github import GithubFeed, GithubFeedRepository
from app.domain.links import Link, LinkRepository
from app.domain.logs import LogFilter, LogFilterRepository, LogSource, LogSourceRepository
from app.domain.repository import Repository
from app.domain.settings import AppSettings, SettingsRepository

BACKUP_VERSION = 1
_UPLOAD_PREFIX = "/images/"


@dataclass(frozen=True)
class BackupSummary:
    """What a backup holds (or what a restore wrote)."""

    created_at: datetime | None
    links: int
    log_sources: int
    log_filters: int
    github_feeds: int
    images: int


def _plain(value: Any) -> Any:
    """An entity field as JSON: datetimes as ISO-8601 UTC text, enums as values."""
    if isinstance(value, datetime):
        return format_timestamp(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _to_dict(entity: Any) -> dict[str, Any]:
    return {item.name: _plain(getattr(entity, item.name)) for item in fields(entity)}


def _check(name: str, value: Any, hint: Any) -> Any:
    """`value` as the field's type wants it; the entity checks its own rules after."""
    optional = isinstance(hint, types.UnionType) and type(None) in typing.get_args(hint)
    if value is None:
        if optional:
            return None
        raise ValidationError(f"Field '{name}' is missing")
    base = next((arg for arg in typing.get_args(hint) if arg is not type(None)), hint) if optional else hint
    origin = typing.get_origin(base) or base
    if base is datetime:
        moment = parse_timestamp(value) if isinstance(value, str) else None
        if moment is None:
            raise ValidationError(f"Field '{name}' must be a timestamp")
        return moment
    if isinstance(origin, type) and issubclass(origin, Enum):
        if not isinstance(value, str):
            raise ValidationError(f"Field '{name}' must be text")
        return value  # the entity turns it into its enum, or rejects it
    if origin is bool:
        ok = isinstance(value, bool)
    elif origin is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif origin is str:
        ok = isinstance(value, str)
    elif origin is list:
        ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    elif origin is dict:
        ok = isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
    else:
        ok = True
    if not ok:
        raise ValidationError(f"Field '{name}' has the wrong type")
    return value


def _from_dict[T](cls: type[T], raw: Any) -> T:
    """An entity from its backup form; missing optional fields take their default."""
    if not isinstance(raw, dict):
        raise ValidationError("Not an object")
    hints = typing.get_type_hints(cls)
    values = {}
    for item in fields(cls):  # type: ignore[arg-type]
        if item.name in raw:
            values[item.name] = _check(item.name, raw[item.name], hints[item.name])
        elif item.default is MISSING and item.default_factory is MISSING:
            raise ValidationError(f"Field '{item.name}' is missing")
    return cls(**values)


def _save_all[T](repository: Repository[T], wanted: list[T]) -> None:
    for entity in wanted:
        repository.save(entity)


def _remove_others[T](repository: Repository[T], wanted: list[T]) -> None:
    keep = {entity.id for entity in wanted}  # type: ignore[attr-defined]
    for entity in repository.list_all():
        if entity.id not in keep:  # type: ignore[attr-defined]
            repository.delete(entity.id)  # type: ignore[attr-defined]


@dataclass
class _Plan:
    links: list[Link]
    sources: list[LogSource]
    filters: list[LogFilter]
    feeds: list[GithubFeed]
    settings: AppSettings
    images: dict[str, tuple[bytes, str]]  # upload URL in the backup -> (content, extension)
    created_at: datetime | None

    def summary(self) -> BackupSummary:
        return BackupSummary(
            created_at=self.created_at,
            links=len(self.links),
            log_sources=len(self.sources),
            log_filters=len(self.filters),
            github_feeds=len(self.feeds),
            images=len(self.images),
        )


class BackupService:
    def __init__(
        self,
        links: LinkRepository,
        sources: LogSourceRepository,
        filters: LogFilterRepository,
        feeds: GithubFeedRepository,
        settings: SettingsRepository,
        images: ImageService,
    ):
        self._links = links
        self._sources = sources
        self._filters = filters
        self._feeds = feeds
        self._settings = settings
        self._images = images

    def create(self) -> dict[str, Any]:
        links = self._links.list_all()
        images = {}
        for link in links:
            if link.image_url.startswith(_UPLOAD_PREFIX) and link.image_url not in images:
                image = self._images.load_image(link.image_url)
                if image is not None:
                    images[link.image_url] = encode_image_uri(*image)
        return {
            "opsdeck_backup": BACKUP_VERSION,
            "created_at": format_timestamp(utc_now()),
            "links": [_to_dict(link) for link in links],
            "log_sources": [_to_dict(source) for source in self._sources.list_all()],
            "log_filters": [_to_dict(item) for item in self._filters.list_all()],
            "github_feeds": [_to_dict(feed) for feed in self._feeds.list_all()],
            "settings": asdict(self._settings.load()),
            "images": images,
        }

    def check(self, backup: Any) -> BackupSummary:
        """What restoring `backup` would write; raises ValidationError when it cannot."""
        return self._plan(backup).summary()

    def restore(self, backup: Any) -> BackupSummary:
        """Replace everything with the backup's content."""
        plan = self._plan(backup)
        urls = {
            old: self._images.save_image(content, f"image.{ext}")
            for old, (content, ext) in plan.images.items()
        }
        for link in plan.links:
            if link.image_url.startswith(_UPLOAD_PREFIX):
                link.image_url = urls.get(link.image_url, "")  # an upload the backup did not carry
        # every item of the backup is written before anything is removed, and
        # sources go last, once no filter points at the ones that leave
        _save_all(self._sources, plan.sources)
        _save_all(self._filters, plan.filters)
        _save_all(self._feeds, plan.feeds)
        _save_all(self._links, plan.links)
        self._settings.save(plan.settings)
        _remove_others(self._filters, plan.filters)
        _remove_others(self._feeds, plan.feeds)
        _remove_others(self._links, plan.links)
        _remove_others(self._sources, plan.sources)
        return plan.summary()

    def _plan(self, backup: Any) -> _Plan:
        if not isinstance(backup, dict):
            raise ValidationError("Not an OpsDeck backup")
        version = backup.get("opsdeck_backup")
        if version is None:
            if "opsdeck" in backup:
                raise ValidationError(
                    "This file is an export of chosen items: import it with ⇅ in the tab it came from"
                )
            raise ValidationError("Not an OpsDeck backup")
        if not isinstance(version, int) or version < 1:
            raise ValidationError("Not an OpsDeck backup")
        if version > BACKUP_VERSION:
            raise ValidationError("This backup comes from a newer OpsDeck; update this one to restore it")
        created = backup.get("created_at")
        plan = _Plan(
            links=self._entities(backup, "links", Link, "card"),
            sources=self._entities(backup, "log_sources", LogSource, "log source"),
            filters=self._entities(backup, "log_filters", LogFilter, "filter"),
            feeds=self._entities(backup, "github_feeds", GithubFeed, "feed"),
            settings=self._settings_of(backup.get("settings")),
            images=self._images_of(backup.get("images")),
            created_at=parse_timestamp(created) if isinstance(created, str) else None,
        )
        source_ids = {source.id for source in plan.sources}
        for log_filter in plan.filters:
            if log_filter.source_id not in source_ids:
                raise ValidationError(f"Filter '{log_filter.name}': its log source is not in the backup")
        return plan

    @staticmethod
    def _entities[T](backup: dict[str, Any], key: str, cls: type[T], label: str) -> list[T]:
        items = backup.get(key, [])
        if not isinstance(items, list):
            raise ValidationError(f"'{key}' must be a list")
        entities, seen = [], set()
        for index, raw in enumerate(items):
            name = raw.get("name") or raw.get("title") if isinstance(raw, dict) else None
            where = (
                f"{label.capitalize()} '{name}'"
                if isinstance(name, str)
                else f"{label.capitalize()} #{index + 1}"
            )
            try:
                entity = _from_dict(cls, raw)
            except (ValidationError, TypeError) as error:
                raise ValidationError(f"{where}: {error}") from None
            entity_id = entity.id  # type: ignore[attr-defined]
            if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
                raise ValidationError(f"{where}: missing or repeated id")
            seen.add(entity_id)
            entities.append(entity)
        return entities

    @staticmethod
    def _settings_of(raw: Any) -> AppSettings:
        if raw is None:
            return AppSettings()
        try:
            return _from_dict(AppSettings, raw)
        except (ValidationError, TypeError) as error:
            raise ValidationError(f"Settings: {error}") from None

    def _images_of(self, raw: Any) -> dict[str, tuple[bytes, str]]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValidationError("'images' must be an object")
        images = {}
        for url, uri in raw.items():
            try:
                content, extension = decode_image_uri(uri)
                self._images.check_image(content, extension)
            except ValidationError as error:
                raise ValidationError(f"Image {url}: {error}") from None
            images[url] = (content, extension)
        return images
