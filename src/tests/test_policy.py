"""Regression specification for the deprecated Core-to-Agate policy boundary.

These tests are intentionally more than behavioral examples. Together they
record the architectural contract established while retiring Core's embedded
hardware-policy authority:

* Agate owns hardware-policy interpretation and verdicts.
* Oramasys composes higher-level routing decisions.
* Core executes approved decisions and must not reacquire policy ownership.
* Core may retain a deprecated compatibility wrapper, but that wrapper must
  delegate to a real Agate ``PolicyStore`` rather than duplicate policy logic.
* Importing Core must not import Agate eagerly or make Agate a package-level
  runtime requirement.
* Core must not publish Agate through either required or optional dependency
  metadata; compatibility testing installs Agate independently as an external
  CI fixture.
* Legacy callers remain behaviorally compatible during the transition.

The dependency-name checks also guard against syntactic aliases permitted by
Python packaging conventions, so the architecture cannot be bypassed merely by
changing punctuation, adding extras, adding a version specifier, or using a
direct reference.
"""
import re
import tomllib

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

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _canonical_requirement_name(requirement: str) -> str:
    """Return the canonical distribution name from a PEP 508-style string.

    Architectural dependency checks must compare package identities rather
    than spelling variants. Extracting the leading distribution token before
    normalizing ``-``, ``_``, and ``.`` prevents version specifiers, extras,
    markers, or direct-reference syntax from obscuring an Agate declaration.
    """
    match = _REQUIREMENT_NAME.match(requirement.strip())
    assert match is not None, f"Unable to parse dependency name: {requirement!r}"
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


@pytest.fixture
def resolver(tmp_path):
    """Build the compatibility resolver through its public file-loading path.

    The fixture deliberately exercises ``from_file()`` so the ordinary tests
    prove that the deprecated Core façade can still consume the historical
    policy format while delegating interpretation to Agate.
    """
    p = tmp_path / "policy.yml"
    p.write_text(POLICY_YAML)
    return HardwarePolicyResolver.from_file(p)


def test_never_raises_hardware_affinity_error(resolver):
    """Enforce Agate's ``NEVER`` verdict as a hard pre-spawn prohibition.

    Compatibility must not weaken the authority boundary: when Agate marks a
    model-tier pairing forbidden, Core's façade must surface that prohibition
    rather than reinterpret, downgrade, or silently route around it.
    """
    with pytest.raises(HardwareAffinityError):
        resolver.check_affinity(model="big-model", target_tier="mac")


def test_prefer_returns_prefer(resolver):
    """Preserve Agate's ``PREFER`` verdict without Core-side reinterpretation.

    A preference is policy data owned by Agate. Core's compatibility surface
    must transmit that decision faithfully instead of introducing an
    independent ranking or placement rule.
    """
    verdict = resolver.check_affinity(model="big-model", target_tier="windows")
    assert verdict == "PREFER"


def test_allow_returns_allow(resolver):
    """Preserve Agate's ordinary ``ALLOW`` verdict exactly.

    This complements the ``PREFER`` and ``NEVER`` cases and demonstrates that
    the wrapper is a transparent policy adapter across the full verdict set,
    not a second policy engine with partially overlapping semantics.
    """
    verdict = resolver.check_affinity(model="big-model", target_tier="shared")
    assert verdict == "ALLOW"


def test_unknown_model_defaults_allow(resolver):
    """Retain the legacy unknown-model fallback while authority migrates.

    Deprecation is not permission for an accidental breaking change. The
    compatibility layer must preserve the historical default for unknown
    models until callers migrate to Agate's native API or a deliberate
    breaking release changes the contract.
    """
    verdict = resolver.check_affinity(model="unknown-model", target_tier="mac")
    assert verdict == "ALLOW"


def test_all_tiers_never_raises(resolver):
    """Reject a model that Agate forbids on every available hardware tier.

    This guards against a dangerous fallback in which Core might manufacture
    an executable placement after the policy authority has prohibited every
    placement. No compatibility path may convert universal ``NEVER`` into a
    permissive default.
    """
    for tier in ("mac", "windows", "shared"):
        with pytest.raises(HardwareAffinityError):
            resolver.check_affinity(model="banned-model", target_tier=tier)


def test_resolve_coding_default(resolver):
    """Resolve the coding default from Agate policy without local duplication.

    The expected model and tier originate in the policy store. Their presence
    here is a delegation regression test: Core may expose the legacy result
    shape, but it must not regain ownership of the routing rule that produced
    it.
    """
    decision = resolver.resolve(task_type="coding", optimize_for="default")
    assert decision.model == "big-model"
    assert decision.hardware_tier == "windows"


def test_resolve_reasoning_speed(resolver):
    """Honor an optimization-specific route supplied by the Agate policy.

    Distinct task/optimization routes demonstrate that the compatibility layer
    is reading the authoritative routing map rather than relying on one
    hard-coded default or on Core-local model-selection heuristics.
    """
    decision = resolver.resolve(task_type="reasoning", optimize_for="speed")
    assert decision.model == "small-model"
    assert decision.hardware_tier == "mac"


def test_resolve_unknown_route_falls_back_to_default(resolver):
    """Preserve the policy-defined default route for unmatched task keys.

    Fallback semantics remain part of the legacy observable contract, but the
    fallback value itself must still come from the authoritative policy store.
    This keeps compatibility behavior stable without recreating policy
    ownership inside Core.
    """
    decision = resolver.resolve(task_type="ops", optimize_for="whatever")
    assert decision.model == "small-model"


def test_resolve_model_hint_respected(resolver):
    """Respect an explicit model hint while retaining policy-controlled fit.

    A caller may name the model, but the compatibility wrapper still derives
    the admissible preferred hardware tier from Agate's model specification.
    The test therefore separates caller intent from hardware-policy authority.
    """
    decision = resolver.resolve(task_type="coding", model_hint="small-model")
    assert decision.model == "small-model"
    assert decision.reason == "explicit_model_hint"


def test_resolve_model_hint_never_tier_raises(resolver):
    """Forbid an explicit hint when policy provides no admissible placement.

    Explicit caller preference must never override Agate's safety boundary. A
    model forbidden on every tier remains unrouteable even when requested
    directly through the legacy Core interface.
    """
    with pytest.raises(HardwareAffinityError):
        resolver.resolve(task_type="coding", model_hint="banned-model")


def test_from_file_emits_deprecation_warning(resolver):
    """Keep the legacy surface usable while making its retirement explicit.

    The resolver fixture already proves construction through ``from_file()``.
    This assertion separately protects the migration signal: callers retain
    compatibility today but receive an explicit instruction to move to Agate
    rather than treating the façade as a permanent Core authority.
    """
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        HardwarePolicyResolver(resolver._store)
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_resolver_delegates_to_a_real_agate_policy_store(resolver):
    """Require the compatibility façade to hold an actual Agate policy store.

    This is the central architectural regression check. A raw mapping or a
    Core-defined substitute could evolve into a second policy implementation;
    requiring ``agate.PolicyStore`` keeps verdict and routing authority in the
    repository that owns that domain.
    """
    import agate

    assert isinstance(resolver._store, agate.PolicyStore)


def test_constructor_accepts_legacy_raw_policy_mapping():
    """Preserve direct construction from the pre-migration raw mapping form.

    PR #5 exposed a compatibility gap: accepting the mapping at construction
    but storing it verbatim deferred failure until methods accessed ``.models``
    or ``.routing``. Normalizing that historical input into Agate's native
    store preserves behavior without creating a parallel adapter model in
    Core.
    """
    import yaml

    raw = yaml.safe_load(POLICY_YAML)
    with pytest.warns(DeprecationWarning):
        resolver = HardwarePolicyResolver(raw)

    assert resolver.check_affinity(model="big-model", target_tier="windows") == "PREFER"
    with pytest.raises(HardwareAffinityError):
        resolver.check_affinity(model="big-model", target_tier="mac")

    decision = resolver.resolve(task_type="reasoning", optimize_for="speed")
    assert decision.model == "small-model"
    assert decision.hardware_tier == "mac"


def test_legacy_raw_policy_mapping_normalizes_to_a_real_policy_store():
    """Prove legacy normalization converges on Agate's canonical data model.

    Backward compatibility must not be implemented with a Core-owned shadow
    structure that merely resembles Agate. Both file-based and raw-mapping
    construction paths must terminate in the same ``PolicyStore`` and
    ``ModelSpec`` abstractions owned by Agate.
    """
    import agate
    import yaml

    raw = yaml.safe_load(POLICY_YAML)
    resolver = HardwarePolicyResolver(raw)

    assert isinstance(resolver._store, agate.PolicyStore)
    assert isinstance(resolver._store.models["big-model"], agate.ModelSpec)


def test_module_has_no_hard_agate_dependency_at_import_time():
    """Keep importing Core independent from importing the policy authority.

    Agate is intentionally lazy and external: simply importing
    ``perpetua_core.policy`` must not import Agate. This prevents the deprecated
    façade from turning a compatibility path into a hard upward dependency at
    module-import time.
    """
    import importlib
    import sys

    for mod_name in list(sys.modules):
        if mod_name == "agate" or mod_name.startswith("agate."):
            del sys.modules[mod_name]

    import perpetua_core.policy
    importlib.reload(perpetua_core.policy)

    assert "agate" not in sys.modules


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("oramasys-agate", "oramasys-agate"),
        ("oramasys_agate>=0.1", "oramasys-agate"),
        ("oramasys.agate[tests]~=0.1", "oramasys-agate"),
        (
            "oramasys_agate @ git+https://github.com/oramasys/agate.git@deadbeef",
            "oramasys-agate",
        ),
    ],
)
def test_requirement_name_parser_canonicalizes_equivalent_spellings(
    requirement, expected
):
    """Treat packaging aliases as one dependency identity for invariants.

    The boundary must survive ordinary PEP 508 spelling variation. Hyphens,
    underscores, dots, extras, version constraints, and direct references may
    change syntax, but none may disguise ``oramasys-agate`` from the metadata
    checks below.
    """
    assert _canonical_requirement_name(requirement) == expected


def test_core_does_not_declare_agate_as_a_runtime_dependency():
    """Forbid Agate from Core's required package dependencies.

    This is the package-level expression of the architectural direction:
    **Agate decides policy -> Oramasys composes -> Core executes.** A required
    Agate dependency would invert that boundary by making Core depend upward
    on the hardware-policy authority it is explicitly retiring.

    Compatibility remains available only when an embedding environment chooses
    to install Agate independently; Core itself must not make that installation
    part of its published runtime contract.
    """
    project = Path(__file__).parents[2] / "pyproject.toml"
    metadata = tomllib.loads(project.read_text(encoding="utf-8"))
    dependency_names = {
        _canonical_requirement_name(dependency)
        for dependency in metadata["project"]["dependencies"]
    }

    assert "oramasys-agate" not in dependency_names


def test_core_does_not_publish_agate_as_an_optional_dependency():
    """Forbid Agate from every optional dependency extra published by Core.

    Removing Agate from ``project.dependencies`` is insufficient if a future
    change quietly reintroduces it through ``project.optional-dependencies``.
    Extras are still Core package metadata and therefore still describe a
    dependency relationship owned and advertised by Core.

    The approved compatibility arrangement is stricter: Agate is pinned in an
    external test-requirements file and installed independently by CI. This
    invariant ensures that test convenience cannot erode the repository
    boundary by migrating that fixture back into Core's published metadata.
    """
    project = Path(__file__).parents[2] / "pyproject.toml"
    metadata = tomllib.loads(project.read_text(encoding="utf-8"))
    optional_dependencies = metadata["project"].get("optional-dependencies", {})
    dependency_names = {
        _canonical_requirement_name(dependency)
        for dependencies in optional_dependencies.values()
        for dependency in dependencies
    }

    assert "oramasys-agate" not in dependency_names
