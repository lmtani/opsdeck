"""Atomic JSON file persistence used by the JSON repositories."""

import json
import os
import tempfile
import threading
from pathlib import Path


class JsonStore:
    """Reads/writes a single JSON document with atomic replace and a lock."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def read(self, default):
        with self._lock:
            if not self._path.exists():
                return default
            return json.loads(self._path.read_text(encoding="utf-8"))

    def write(self, data) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=self._path.parent, prefix=self._path.name, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._path)
            except BaseException:
                os.unlink(tmp_path)
                raise
