"""Canonical port of perpetua_tools.discovery (v1-legacy → v2-planning, 2026-05-17).

Verbatim copy from `diazMelgarejo/Perpetua-Tools/perpetua/discovery/`.
v2 is the canonical home from this commit forward. A future plan will
flip v1 to import from here instead of duplicating.
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
