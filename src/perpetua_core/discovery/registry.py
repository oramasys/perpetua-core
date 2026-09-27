from __future__ import annotations
from .backend import Backend, BackendHealth


class BackendRegistry:
    """In-memory backend observations. Network probing lives in composition."""

    def __init__(self) -> None:
        """Start with no observations."""
        self._backends: dict[str, Backend] = {}

    def all(self) -> list[Backend]:
        """Return every stored observation, including offline ones."""
        return list(self._backends.values())

    def online(self) -> list[Backend]:
        """Advisory routing set. ``ONLINE`` here does not authorize a dial."""
        return [b for b in self._backends.values() if b.health is BackendHealth.ONLINE]

    def find(self, name: str) -> Backend | None:
        """Return the observation stored under ``name``."""
        return self._backends.get(name)

    def record(self, backend: Backend) -> None:
        """Store an advisory observation, replacing any previous one with the same name.

        A later ``OFFLINE`` record revokes a stale ``ONLINE`` value for that name.
        This method does not probe or authorize a dial.
        """
        self._backends[backend.name] = backend
