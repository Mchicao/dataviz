"""Object storage backend abstraction and in-memory test double.

The DataVIZ service never talks to a concrete cloud SDK directly. All artifact
persistence flows through the :class:`ObjectBackend` protocol so the service can
run against any store (S3, Azure Blob, local FS, or the in-memory test double)
without the storage layer depending on a vendor SDK. Production implementations
live behind this protocol; tests use :class:`InMemoryObjectBackend`.

The backend is intentionally a *dumb byte store*: it does NOT enforce tenant
isolation or version immutability. Those boundaries are enforced by
:class:`~apps.dataviz_service.storage.artifacts.ArtifactService`, which composes
tenant-led keys and rejects overwrites. Keeping the backend dumb lets the same
protocol back both scratch space and immutable artifact storage, and keeps the
isolation logic in one auditable place.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from apps.dataviz_service.contracts import ResourceNotFoundError


@runtime_checkable
class ObjectBackend(Protocol):
    """Minimal byte-store protocol implemented by every storage adapter."""

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Store ``data`` only when ``key`` is absent; return whether it was created."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key`` or raise :class:`ResourceNotFoundError`."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key`` or raise :class:`ResourceNotFoundError` if absent."""
        ...

    def exists(self, key: str) -> bool:
        """Return ``True`` iff ``key`` is currently stored."""
        ...

    def list(self, prefix: str) -> list[str]:
        """Return the sorted keys whose path starts with ``prefix``."""
        ...


class InMemoryObjectBackend:
    """Thread-safe in-memory test double for :class:`ObjectBackend`.

    Use only in tests. Production code must depend on the :class:`ObjectBackend`
    protocol, never on this concrete class.
    """

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def put_if_absent(self, key: str, data: bytes) -> bool:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        with self._lock:
            if key in self._objects:
                return False
            self._objects[key] = bytes(data)
            return True

    def get(self, key: str) -> bytes:
        with self._lock:
            if key not in self._objects:
                raise ResourceNotFoundError("object", key)
            return self._objects[key]

    def delete(self, key: str) -> None:
        with self._lock:
            if key not in self._objects:
                raise ResourceNotFoundError("object", key)
            del self._objects[key]

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self._objects if k.startswith(prefix))

    def _snapshot(self) -> dict[str, bytes]:
        """Return a shallow copy of the stored objects (test inspections only)."""
        with self._lock:
            return dict(self._objects)
