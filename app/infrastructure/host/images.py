"""Stores uploaded card images on the local filesystem."""

import re
import uuid
from pathlib import Path

from app.domain.links import ImageStore

# the names `save` gives out; anything else (e.g. "../x") is never read
_SAVED_NAME = re.compile(r"[0-9a-f]{32}\.([a-z]{3,4})")


class LocalImageStore(ImageStore):
    def __init__(self, directory: Path, url_prefix: str = "/images"):
        self._directory = directory
        self._url_prefix = url_prefix.rstrip("/")

    def save(self, content: bytes, extension: str) -> str:
        self._directory.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{extension}"
        (self._directory / filename).write_bytes(content)
        return f"{self._url_prefix}/{filename}"

    def load(self, url: str) -> tuple[bytes, str] | None:
        prefix = f"{self._url_prefix}/"
        if not url.startswith(prefix):
            return None
        match = _SAVED_NAME.fullmatch(url[len(prefix) :])
        path = self._directory / url[len(prefix) :]
        if match is None or not path.is_file():
            return None
        return path.read_bytes(), match.group(1)
