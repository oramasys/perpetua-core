"""Mirror-exclusion tests for perpetua_core discovery selector.

Verifies hardware safety policy: lmstudio-mac is NEVER selected for
inference dispatch regardless of model presence or tier. See LESSONS.md
2026-05-17 entry: "HARD POLICY: Mac inference via Ollama only".
"""
from datetime import datetime, timezone

import pytest

from perpetua_core.discovery.backend import Backend, BackendHealth, BackendKind
from perpetua_core.discovery.errors import NoBackendAvailableError
from perpetua_core.discovery.registry import BackendRegistry
from perpetua_core.discovery.selector import select_backend

NOW = datetime.now(timezone.utc)


def _online(name: str, url: str, kind: BackendKind, models: list[str]) -> Backend:
    return Backend(name, url, kind, tuple(models), BackendHealth.ONLINE, NOW)


@pytest.fixture
def reg():
    r = BackendRegistry()
    r._backends["ollama-local"] = _online(
        "ollama-local", "http://localhost:11434/v1", BackendKind.OLLAMA, ["qwen3.5:9b-nvfp4"]
    )
    r._backends["lmstudio-win"] = _online(
        "lmstudio-win",
        "http://192.168.254.103:1234/v1",
        BackendKind.LMSTUDIO,
        ["qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2"],
    )
    return r


@pytest.fixture
def reg_with_mirror(reg):
    reg._backends["lmstudio-mac"] = _online(
        "lmstudio-mac",
        "http://localhost:1234/v1",
        BackendKind.LMSTUDIO,
        ["qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2"],
    )
    return reg


def test_basic_routing_mac_tier_uses_ollama(reg):
    b = select_backend(reg, model_hint=None, task_type="coding", target_tier="mac")
    assert b.name == "ollama-local"


def test_basic_routing_windows_tier_uses_lmstudio_win(reg):
    b = select_backend(reg, model_hint=None, task_type="coding", target_tier="windows")
    assert b.name == "lmstudio-win"


def test_mirror_never_selected_for_mac_tier(reg_with_mirror):
    b = select_backend(reg_with_mirror, model_hint=None, task_type="coding", target_tier="mac")
    assert b.name == "ollama-local"


def test_mirror_never_selected_for_shared_coding(reg_with_mirror):
    """shared+coding prefers LMSTUDIO but must pick lmstudio-win, not the mirror."""
    b = select_backend(reg_with_mirror, model_hint=None, task_type="coding", target_tier="shared")
    assert b.name == "lmstudio-win"


def test_mirror_excluded_even_when_only_lmstudio_online():
    """If lmstudio-win is offline, shared+coding falls back to Ollama, never the mirror."""
    r = BackendRegistry()
    r._backends["ollama-local"] = _online(
        "ollama-local", "http://localhost:11434/v1", BackendKind.OLLAMA, ["qwen3.5:9b-nvfp4"]
    )
    r._backends["lmstudio-mac"] = _online(
        "lmstudio-mac", "http://localhost:1234/v1", BackendKind.LMSTUDIO, ["heavy-model"]
    )
    b = select_backend(r, model_hint=None, task_type="coding", target_tier="shared")
    assert b.name == "ollama-local"  # graceful fallback, not the mirror


def test_model_hint_skips_mirror():
    """model_hint must not route to a mirror even if model appears there."""
    r = BackendRegistry()
    r._backends["lmstudio-mac"] = _online(
        "lmstudio-mac",
        "http://localhost:1234/v1",
        BackendKind.LMSTUDIO,
        ["qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2"],
    )
    with pytest.raises(NoBackendAvailableError):
        select_backend(
            r,
            model_hint="qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2",
            task_type="reasoning",
            target_tier="shared",
        )
