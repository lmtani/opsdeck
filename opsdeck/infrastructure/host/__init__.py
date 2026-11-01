"""Adapters on the machine running the app: ImageStore (uploaded card images
on disk)."""

from opsdeck.infrastructure.host.images import LocalImageStore

__all__ = ["LocalImageStore"]
