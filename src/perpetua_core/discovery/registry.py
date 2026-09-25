from __future__ import annotations
from .backend import Backend, BackendHealth


class BackendRegistry:
    """In-memory backend observations. Network probing lives in composition."""

    def __init__(self) -> None:
        self._backends: dict[str, Backend] = {}

    def all(self) -> list[Backend]:
        return list(self._backends.values())

    def online(self) -> list[Backend]:
        return [b for b in self._backends.values() if b.health is BackendHealth.ONLINE]

    def find(self, name: str) -> Backend | None:
        return self._backends.get(name)

    def record(self, backend: Backend) -> None:
        """Store a backend observation without performing network I/O."""
        self._backends[backend.name] = backend
