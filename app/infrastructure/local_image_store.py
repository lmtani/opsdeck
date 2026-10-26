"""Stores uploaded card images on the local filesystem."""

import uuid
from pathlib import Path

from app.domain.links import ImageStore


class LocalImageStore(ImageStore):
    def __init__(self, directory: Path, url_prefix: str = "/images"):
        self._directory = directory
        self._url_prefix = url_prefix.rstrip("/")

    def save(self, content: bytes, extension: str) -> str:
        self._directory.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{extension}"
        (self._directory / filename).write_bytes(content)
        return f"{self._url_prefix}/{filename}"
