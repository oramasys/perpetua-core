"""Contract test: canonical discovery module exports the full v1 surface."""
from perpetua_core.discovery import (
    Backend, BackendKind, BackendHealth, BackendRegistry,
    select_backend, BackendOfflineError, NoBackendAvailableError,
)


def test_canonical_discovery_module_exports_full_v1_surface():
    # If this test compiles + imports succeed, the API surface matches v1.
    assert hasattr(BackendRegistry, "autodetect")
    assert hasattr(BackendRegistry, "register_by_ip")
    assert callable(select_backend)
    # Enum values match
    assert BackendKind.OLLAMA.value == "ollama"
    assert BackendKind.LMSTUDIO.value == "lmstudio"
    assert BackendHealth.ONLINE.value == "online"
    # Error types are RuntimeError subclasses (so they propagate cleanly)
    assert issubclass(BackendOfflineError, RuntimeError)
    assert issubclass(NoBackendAvailableError, RuntimeError)


def test_canonical_backend_dataclass_has_expected_fields():
    b = Backend(
        name="t", base_url="http://x:1234/v1", kind=BackendKind.LMSTUDIO,
        models=("m",), health=BackendHealth.UNKNOWN, last_seen=None,
    )
    assert b.is_targetable_by_ip("x") is True
    assert b.host == "x"
