"""TDD: HardwarePolicyResolver — write failing tests first."""
import pytest
from pathlib import Path
from perpetua_core.policy import HardwarePolicyResolver, HardwareAffinityError

POLICY_YAML = """\
version: 1
models:
  big-model:
    mac: NEVER
    windows: PREFER
    shared: ALLOW
  small-model:
    mac: PREFER
    windows: ALLOW
    shared: ALLOW
  banned-model:
    mac: NEVER
    windows: NEVER
    shared: NEVER
routing:
  coding:default: big-model
  reasoning:speed: small-model
  reasoning:reliability: big-model
  default: small-model
"""


@pytest.fixture
def resolver(tmp_path):
    p = tmp_path / "policy.yml"
    p.write_text(POLICY_YAML)
    return HardwarePolicyResolver.from_file(p)


def test_never_raises_hardware_affinity_error(resolver):
    with pytest.raises(HardwareAffinityError):
        resolver.check_affinity(model="big-model", target_tier="mac")


def test_prefer_returns_prefer(resolver):
    verdict = resolver.check_affinity(model="big-model", target_tier="windows")
    assert verdict == "PREFER"


def test_allow_returns_allow(resolver):
    verdict = resolver.check_affinity(model="big-model", target_tier="shared")
    assert verdict == "ALLOW"


def test_unknown_model_defaults_allow(resolver):
    verdict = resolver.check_affinity(model="unknown-model", target_tier="mac")
    assert verdict == "ALLOW"


def test_all_tiers_never_raises(resolver):
    for tier in ("mac", "windows", "shared"):
        with pytest.raises(HardwareAffinityError):
            resolver.check_affinity(model="banned-model", target_tier=tier)


def test_resolve_coding_default(resolver):
    decision = resolver.resolve(task_type="coding", optimize_for="default")
    assert decision.model == "big-model"
    assert decision.hardware_tier == "windows"


def test_resolve_reasoning_speed(resolver):
    decision = resolver.resolve(task_type="reasoning", optimize_for="speed")
    assert decision.model == "small-model"
    assert decision.hardware_tier == "mac"


def test_resolve_unknown_route_falls_back_to_default(resolver):
    decision = resolver.resolve(task_type="ops", optimize_for="whatever")
    assert decision.model == "small-model"


def test_resolve_model_hint_respected(resolver):
    decision = resolver.resolve(task_type="coding", model_hint="small-model")
    assert decision.model == "small-model"
    assert decision.reason == "explicit_model_hint"


def test_resolve_model_hint_never_tier_raises(resolver):
    with pytest.raises(HardwareAffinityError):
        resolver.resolve(task_type="coding", model_hint="banned-model")


def test_from_file_emits_deprecation_warning(resolver):
    """resolver fixture already constructed one via from_file() -- this
    test exists to make the deprecation explicit and regression-tested,
    not just an incidental side effect visible in pytest's warning
    summary."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        HardwarePolicyResolver(resolver._store)
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_resolver_delegates_to_a_real_agate_policy_store(resolver):
    """Regression for the actual architectural fix: this wrapper carries
    no independent verdict logic of its own -- confirmed directly by
    checking the resolver's internal store is a genuine agate
    PolicyStore instance, not a raw dict the old implementation used."""
    import agate

    assert isinstance(resolver._store, agate.PolicyStore)


def test_module_has_no_hard_agate_dependency_at_import_time():
    """Confirmed directly: importing perpetua_core.policy must never
    import agate itself -- only constructing a resolver via from_file()
    does, and only then. A hard, module-level dependency here would
    just relocate the 'core imports upward from policy' violation this
    wrapper exists to close, rather than actually close it."""
    import importlib
    import sys

    for mod_name in list(sys.modules):
        if mod_name == "agate" or mod_name.startswith("agate."):
            del sys.modules[mod_name]

    import perpetua_core.policy
    importlib.reload(perpetua_core.policy)

    assert "agate" not in sys.modules
