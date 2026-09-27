"""Contract test: pure discovery surface retained in the kernel."""
import ast
from pathlib import Path
from perpetua_core.discovery import (
    Backend,
    BackendKind,
    BackendHealth,
    BackendRegistry,
    select_backend,
    BackendOfflineError,
    NoBackendAvailableError,
)


def test_canonical_discovery_module_exports_pure_surface():
    assert callable(select_backend)
    assert callable(BackendRegistry.record)
    assert not hasattr(BackendRegistry, "autodetect")
    assert not hasattr(BackendRegistry, "register_by_ip")
    assert BackendKind.OLLAMA.value == "ollama"
    assert BackendKind.LMSTUDIO.value == "lmstudio"
    assert BackendHealth.ONLINE.value == "online"
    assert issubclass(BackendOfflineError, RuntimeError)
    assert issubclass(NoBackendAvailableError, RuntimeError)


def test_canonical_backend_dataclass_has_expected_fields():
    b = Backend(
        name="t",
        base_url="http://x:1234/v1",
        kind=BackendKind.LMSTUDIO,
        models=("m",),
        health=BackendHealth.UNKNOWN,
        last_seen=None,
    )
    assert b.is_targetable_by_ip("x") is True
    assert b.host == "x"


def test_registry_record_and_online_query():
    registry = BackendRegistry()
    online = Backend(
        name="ollama-local",
        base_url="http://127.0.0.1:11434/v1",
        kind=BackendKind.OLLAMA,
        models=("m",),
        health=BackendHealth.ONLINE,
    )
    offline = Backend(
        name="down",
        base_url="http://127.0.0.1:9/v1",
        kind=BackendKind.OLLAMA,
        models=(),
        health=BackendHealth.OFFLINE,
    )
    registry.record(online)
    registry.record(offline)
    assert registry.find("ollama-local") is online
    assert registry.online() == [online]
    assert {b.name for b in registry.all()} == {"ollama-local", "down"}


def _imports_removed_telos(name: str) -> bool:
    """True for ``telos`` and the removed ``oramasys_telos`` distribution."""
    root = name.split(".", 1)[0]
    return root.startswith("telos") or root == "oramasys_telos"


def _tree_imports_removed_telos(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_imports_removed_telos(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _imports_removed_telos(node.module):
                return True
    return False


def test_core_discovery_has_no_telos_dependency():
    """C0 REJECT_CORE_TELOS_DEP: kernel discovery must stay Telos-free."""
    import perpetua_core.discovery as discovery
    import perpetua_core.discovery.backend as backend
    import perpetua_core.discovery.registry as registry
    import perpetua_core.discovery.selector as selector
    import perpetua_core.discovery.errors as errors

    for mod in (discovery, backend, registry, selector, errors):
        assert "telos" not in mod.__dict__
        assert "oramasys_telos" not in mod.__dict__
        tree = ast.parse(Path(mod.__file__).read_text())
        assert not _tree_imports_removed_telos(tree)


def test_telos_import_guard_rejects_removed_namespace():
    """``import`` and ``from ... import`` of the removed package must fail."""
    forbidden = (
        "import telos\n",
        "import telos.health\n",
        "import oramasys_telos\n",
        "import oramasys_telos.probe\n",
        "from telos import health_probe\n",
        "from oramasys_telos import health_probe\n",
        "from oramasys_telos.probe import health_probe\n",
    )
    for source in forbidden:
        assert _tree_imports_removed_telos(ast.parse(source))
    assert not _tree_imports_removed_telos(ast.parse("import perpetua_core.discovery\n"))
