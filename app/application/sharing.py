"""Use cases of sharing: cards, filters and feeds exported to a portable
bundle and imported from one, with a review step in between.

A bundle carries only the editable fields of each item (no id, position,
creation time or reading mark), so it means the same on any OpsDeck.
Uploaded card images travel inside it as data URIs, and a filter carries its
log source (name, kind and config) instead of the source's id: an import
reuses the source that reads the same place, or creates it. Every item is checked
by building its entity, so the rules stay in the domain; an import builds
all the chosen items before it writes any of them.
"""

import base64
import binascii
import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.application.links import ImageService
from app.domain.common import utc_now
from app.domain.errors import NotFoundError, ValidationError
from app.domain.github import GithubFeed, GithubFeedRepository
from app.domain.links import Link, LinkKind, LinkRepository
from app.domain.logs import LogFilter, LogFilterRepository, LogSource, LogSourceKind, LogSourceRepository
from app.domain.ordering import next_position, ordered
from app.domain.repository import Repository

BUNDLE_VERSION = 2  # 2: filters carry their log source; 1 had a GCP `project`

_IMAGE_MIME = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp", "svg": "svg+xml"}
_MIME_IMAGE = {"png": "png", "jpeg": "jpg", "gif": "gif", "webp": "webp", "svg+xml": "svg"}


class ItemType(StrEnum):
    LINK = "link"
    FILTER = "filter"
    FEED = "feed"


class ImportStatus(StrEnum):
    NEW = "new"
    CONFLICT = "conflict"  # an item of the same type already has this name
    INVALID = "invalid"


class ImportAction(StrEnum):
    CREATE = "create"  # a new item, renamed "Name (2)" when the name is taken
    REPLACE = "replace"  # overwrite the existing item of the same name


@dataclass
class Bundle:
    """The portable file: one list of field dicts per item type."""

    links: list[dict[str, Any]] = field(default_factory=list)
    log_filters: list[dict[str, Any]] = field(default_factory=list)
    github_feeds: list[dict[str, Any]] = field(default_factory=list)
    version: int = BUNDLE_VERSION
    exported_at: datetime | None = None


@dataclass
class ImportCheck:
    """What importing one item of a bundle would do."""

    type: ItemType
    index: int  # position in its bundle list
    name: str
    detail: str  # URL or command, log source, org and repos
    status: ImportStatus
    error: str = ""
    command: str = ""  # a command card's command line, shown before it is imported


@dataclass(frozen=True)
class ImportChoice:
    type: ItemType
    index: int
    action: ImportAction


@dataclass
class ImportResult:
    created: list[tuple[ItemType, str]] = field(default_factory=list)  # (type, id)
    replaced: list[tuple[ItemType, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _Section:
    """How one item type maps to its bundle list, entity and repository."""

    type: ItemType
    key: str  # the bundle list
    fields: dict[str, builtins.type]  # the portable fields and their JSON types
    name_field: str
    repository: Repository[Any]
    build: Callable[..., Any]  # Entity.new
    detail: Callable[[Any], str]


@dataclass
class _Prepared:
    section: _Section
    index: int
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    image: tuple[bytes, str] | None = None
    entity: Any = None
    error: str = ""
    detail: str = ""  # overrides the section's detail
    new_source: LogSource | None = None  # a filter's source, missing here


def _link_detail(link: Link) -> str:
    return link.command if link.kind is LinkKind.COMMAND else link.url


def _feed_detail(feed: GithubFeed) -> str:
    if not feed.repos:
        return f"{feed.org} · whole org"
    return f"{feed.org} · {', '.join(feed.repos)}"


def _source_of(raw: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """(name, kind, config) of a bundled filter's source; version 1 bundles
    only had the GCP project."""
    source = raw.get("source")
    if source is None and isinstance(raw.get("project"), str):
        project = raw["project"].strip()
        return project, LogSourceKind.GCP.value, {"project": project}
    if not isinstance(source, dict):
        raise ValidationError("The filter has no log source")
    name, kind, config = source.get("name"), source.get("kind"), source.get("config")
    if not isinstance(name, str) or not isinstance(kind, str) or not isinstance(config, dict):
        raise ValidationError("The filter's log source needs a name, a kind and a config")
    return name, kind, config


def _check_type(name: str, value: Any, expected: type) -> None:
    if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValidationError(f"Field '{name}' must be a number")
    if expected is bool and not isinstance(value, bool):
        raise ValidationError(f"Field '{name}' must be true or false")
    if expected is str and not isinstance(value, str):
        raise ValidationError(f"Field '{name}' must be text")
    if expected is list and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise ValidationError(f"Field '{name}' must be a list of text")


def _portable_fields(section: _Section, item: Any) -> dict[str, Any]:
    """The known fields of a bundle item, type-checked; unknown keys are ignored."""
    if not isinstance(item, dict):
        raise ValidationError("Not an object")
    values = {name: item[name] for name in section.fields if name in item and item[name] is not None}
    for name, value in values.items():
        _check_type(name, value, section.fields[name])
    return values


def decode_image_uri(uri: Any) -> tuple[bytes, str]:
    if not isinstance(uri, str) or not uri.startswith("data:image/") or ";base64," not in uri:
        raise ValidationError("Image must be a base64 data URI")
    head, data = uri.split(",", 1)
    extension = _MIME_IMAGE.get(head.removeprefix("data:image/").removesuffix(";base64"))
    if extension is None:
        raise ValidationError("Unsupported image type")
    try:
        return base64.b64decode(data, validate=True), extension
    except binascii.Error:
        raise ValidationError("Image is not valid base64") from None


def encode_image_uri(content: bytes, extension: str) -> str:
    return f"data:image/{_IMAGE_MIME[extension]};base64,{base64.b64encode(content).decode()}"


def _free_name(name: str, taken: set[str]) -> str:
    """`name`, or "name (2)", "name (3)"... when another item already uses it."""
    candidate, copy = name, 2
    while candidate.casefold() in taken:
        candidate, copy = f"{name} ({copy})", copy + 1
    return candidate


class SharingService:
    def __init__(
        self,
        links: LinkRepository,
        filters: LogFilterRepository,
        sources: LogSourceRepository,
        feeds: GithubFeedRepository,
        images: ImageService,
    ):
        self._images = images
        self._sources = sources
        self._sections = {
            ItemType.LINK: _Section(
                type=ItemType.LINK,
                key="links",
                fields=dict(
                    title=str,
                    kind=str,
                    url=str,
                    command=str,
                    description=str,
                    image_url=str,
                    category=str,
                    accent=str,
                ),
                name_field="title",
                repository=links,
                build=Link.new,
                detail=_link_detail,
            ),
            ItemType.FILTER: _Section(
                type=ItemType.FILTER,
                key="log_filters",
                fields=dict(
                    name=str,
                    expression=str,
                    freshness=str,
                    limit=int,
                    description=str,
                    line_format=str,
                ),
                name_field="name",
                repository=filters,
                build=LogFilter.new,
                detail=self._source_name,
            ),
            ItemType.FEED: _Section(
                type=ItemType.FEED,
                key="github_feeds",
                fields=dict(
                    name=str,
                    org=str,
                    repos=list,
                    event_kinds=list,
                    hide_own=bool,
                    hide_bots=bool,
                    description=str,
                ),
                name_field="name",
                repository=feeds,
                build=GithubFeed.new,
                detail=_feed_detail,
            ),
        }

    # ----- export ---------------------------------------------------------

    def export(self, link_ids: list[str], filter_ids: list[str], feed_ids: list[str]) -> Bundle:
        """The chosen items in the user's order; an unknown id is a NotFoundError."""
        bundle = Bundle(exported_at=utc_now())
        wanted = {ItemType.LINK: link_ids, ItemType.FILTER: filter_ids, ItemType.FEED: feed_ids}
        for item_type, ids in wanted.items():
            section = self._sections[item_type]
            by_id = {entity.id: entity for entity in section.repository.list_all()}
            missing = [item_id for item_id in ids if item_id not in by_id]
            if missing:
                raise NotFoundError(f"{item_type.capitalize()} '{missing[0]}' not found")
            chosen = ordered([by_id[item_id] for item_id in dict.fromkeys(ids)])
            getattr(bundle, section.key).extend(self._export_item(section, entity) for entity in chosen)
        return bundle

    def _export_item(self, section: _Section, entity: Any) -> dict[str, Any]:
        values = {name: getattr(entity, name) for name in section.fields}
        if section.type is ItemType.LINK and values["image_url"]:
            image = self._images.load_image(values["image_url"])
            if image is not None:  # an upload: the URL means nothing elsewhere
                values["image_url"] = ""
                values["image"] = encode_image_uri(*image)
        if section.type is ItemType.FILTER:
            source = self._sources.get(entity.source_id)
            if source is not None:
                values["source"] = {"name": source.name, "kind": source.kind.value, "config": source.config}
        return values

    # ----- import ---------------------------------------------------------

    def check(self, bundle: Bundle) -> list[ImportCheck]:
        """One check per bundle item: new, a name conflict or invalid (and why)."""
        checks = []
        for prepared in self._prepare(bundle):
            section = prepared.section
            if prepared.error:
                status = ImportStatus.INVALID
            elif self._existing(section, prepared.name) is not None:
                status = ImportStatus.CONFLICT
            else:
                status = ImportStatus.NEW
            entity = prepared.entity
            is_command = (
                section.type is ItemType.LINK and entity is not None and entity.kind is LinkKind.COMMAND
            )
            checks.append(
                ImportCheck(
                    type=section.type,
                    index=prepared.index,
                    name=prepared.name,
                    detail=prepared.detail or (section.detail(entity) if entity is not None else ""),
                    status=status,
                    error=prepared.error,
                    command=entity.command if is_command else "",
                )
            )
        return checks

    def import_bundle(self, bundle: Bundle, choices: list[ImportChoice]) -> ImportResult:
        """Write the chosen items; items left out of `choices` are skipped.

        Every chosen item is validated (and each replace target found)
        before the first write, so a bad item imports nothing.
        """
        prepared = {(item.section.type, item.index): item for item in self._prepare(bundle)}
        plan: list[tuple[_Prepared, Any]] = []  # (item, the entity it replaces or None)
        for choice in dict.fromkeys(choices):
            item = prepared.get((choice.type, choice.index))
            if item is None:
                raise ValidationError(f"The file has no {choice.type} #{choice.index + 1}")
            label = f"{choice.type.capitalize()} '{item.name or choice.index + 1}'"
            if item.error:
                raise ValidationError(f"{label}: {item.error}")
            target = None
            if choice.action is ImportAction.REPLACE:
                target = self._existing(item.section, item.name)
                if target is None:
                    raise ValidationError(f"{label}: nothing to replace")
            plan.append((item, target))

        result = ImportResult()
        taken = {
            item_type: {getattr(e, s.name_field).casefold() for e in s.repository.list_all()}
            for item_type, s in self._sections.items()
        }
        for item, target in plan:
            section, values = item.section, dict(item.fields)
            if item.new_source is not None:
                values["source_id"] = self._create_source(item.new_source)
            if item.image is not None:
                values["image_url"] = self._images.save_image(item.image[0], f"image.{item.image[1]}")
            if target is not None:
                target.update(**values)
                section.repository.save(target)
                result.replaced.append((section.type, target.id))
                continue
            values[section.name_field] = _free_name(item.name, taken[section.type])
            taken[section.type].add(values[section.name_field].casefold())
            position = next_position(section.repository.list_all())
            entity = section.build(position=position, **values)
            section.repository.save(entity)
            result.created.append((section.type, entity.id))
        return result

    def _source_name(self, log_filter: LogFilter) -> str:
        source = self._sources.get(log_filter.source_id)
        return source.name if source is not None else ""

    def _create_source(self, wanted: LogSource) -> str:
        """The id of a source reading what `wanted` reads: one created by an
        earlier item of this import, or `wanted` itself, saved under a free name."""
        for source in self._sources.list_all():
            if source.same_place(wanted.kind, wanted.config):
                return source.id
        taken = {source.name.casefold() for source in self._sources.list_all()}
        source = LogSource.new(name=_free_name(wanted.name, taken), kind=wanted.kind, config=wanted.config)
        self._sources.save(source)
        return source.id

    def _prepare(self, bundle: Bundle) -> list[_Prepared]:
        if not 1 <= bundle.version <= BUNDLE_VERSION:
            if bundle.version > BUNDLE_VERSION:
                raise ValidationError("This file comes from a newer OpsDeck; update this one to import it")
            raise ValidationError("Not an OpsDeck export")
        items = []
        for section in self._sections.values():
            for index, raw in enumerate(getattr(bundle, section.key)):
                items.append(self._prepare_item(section, index, raw))
        return items

    def _prepare_item(self, section: _Section, index: int, raw: Any) -> _Prepared:
        name = raw.get(section.name_field) if isinstance(raw, dict) else None
        prepared = _Prepared(section, index, name.strip() if isinstance(name, str) else "")
        try:
            prepared.fields = _portable_fields(section, raw)
            if section.type is ItemType.LINK:
                prepared.image = self._prepare_image(prepared.fields, raw)
            if section.type is ItemType.FILTER:
                self._prepare_source(prepared, raw)
            prepared.entity = section.build(**prepared.fields)
        except ValidationError as error:
            prepared.error = str(error)
        return prepared

    def _prepare_source(self, prepared: _Prepared, raw: dict[str, Any]) -> None:
        """Point the filter at the source that reads the same place, or plan
        to create that source (validated now, saved on import)."""
        name, kind, config = _source_of(raw)
        for source in self._sources.list_all():
            if source.same_place(kind, config):
                prepared.fields["source_id"] = source.id
                prepared.detail = source.name
                return
        prepared.new_source = LogSource.new(name=name, kind=kind, config=config)
        prepared.fields["source_id"] = prepared.new_source.id  # stands in until the import
        prepared.detail = f"{prepared.new_source.name} · new log source"

    def _prepare_image(self, values: dict[str, Any], raw: dict[str, Any]) -> tuple[bytes, str] | None:
        if raw.get("image"):
            content, extension = decode_image_uri(raw["image"])
            self._images.check_image(content, extension)
            values["image_url"] = ""
            return content, extension
        if values.get("image_url", "").startswith("/"):
            values["image_url"] = ""  # an upload of the other OpsDeck, missing here
        return None

    def _existing(self, section: _Section, name: str) -> Any:
        wanted = name.casefold()
        matches = [
            e for e in section.repository.list_all() if getattr(e, section.name_field).casefold() == wanted
        ]
        return ordered(matches)[0] if matches else None
