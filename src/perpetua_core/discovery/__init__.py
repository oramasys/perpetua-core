"""Canonical discovery surface: pure backend types + selection.

Network health probing and registry I/O belong to Oramasys composition,
not this kernel package.
"""
from .backend import Backend, BackendKind, BackendHealth
from .registry import BackendRegistry
from .selector import select_backend
from .errors import BackendOfflineError, NoBackendAvailableError

__all__ = [
    "Backend",
    "BackendKind",
    "BackendHealth",
    "BackendRegistry",
    "select_backend",
    "BackendOfflineError",
    "NoBackendAvailableError",
]
