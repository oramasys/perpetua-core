"""Deprecated: hardware-affinity policy now lives in oramasys/agate.

This module is a thin, backward-compatible wrapper only. It carries no
independent hardware-policy logic -- every verdict and routing decision
comes from a real agate PolicyStore, loaded lazily (only when a
HardwarePolicyResolver is actually constructed, never at import time)
so perpetua-core's own package has no hard install-time dependency on
agate. That distinction matters: a hard dependency here would just
relocate the "core imports upward from policy" architectural violation
this module used to embed, rather than close it -- confirmed against
PT's own recorded migration invariant ("Core never imports upward from
policy/application repositories") before designing this shape.

New code should depend on `oramasys-agate` directly and use
`agate.load_policy()` / `agate.PolicyStore` -- not this module. This
wrapper exists only so callers written against the old API keep
working during the transition.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Verdict = Literal["ALLOW", "PREFER", "NEVER"]

_TIERS = ("mac", "windows", "shared")


class HardwareAffinityError(RuntimeError):
    """Pre-spawn hardware affinity gate failure."""


@dataclass(frozen=True)
class HardwareDecision:
    provider: str
    hardware_tier: str
    model: str
    reason: str


def _import_agate():
    try:
        import agate
    except ImportError as exc:
        raise ImportError(
            "HardwarePolicyResolver has moved to oramasys/agate. Install "
            "the 'oramasys-agate' package and use agate.load_policy() / "
            "agate.PolicyStore directly -- this perpetua_core.policy "
            "module is a deprecated compatibility wrapper only, and "
            "requires agate to be installed to function."
        ) from exc
    return agate


class HardwarePolicyResolver:
    """Deprecated thin wrapper around agate.PolicyStore. See module
    docstring. Every method here delegates to a real agate PolicyStore
    loaded from the same policy file this class always accepted --
    agate's schema is the same shape this module's own tests already
    exercised, so no data migration is needed, only a delegation of
    the logic that reads it."""

    def __init__(self, store) -> None:
        self._store = store
        warnings.warn(
            "perpetua_core.policy.HardwarePolicyResolver is deprecated; "
            "use agate.load_policy() / agate.PolicyStore directly.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "HardwarePolicyResolver":
        agate = _import_agate()
        return cls(agate.load_policy(path))

    def check_affinity(self, *, model: str, target_tier: str) -> Verdict:
        spec = self._store.models.get(model)
        if spec is None:
            return "ALLOW"
        verdict = spec.verdict_for_tier(target_tier)
        if verdict == "NEVER":
            raise HardwareAffinityError(
                f"Model '{model}' is forbidden on tier '{target_tier}'"
            )
        return verdict

    def resolve(
        self,
        *,
        task_type: str,
        optimize_for: str = "reliability",
        model_hint: str | None = None,
    ) -> HardwareDecision:
        models = self._store.models
        routing = self._store.routing

        if model_hint:
            spec = models.get(model_hint)
            if spec is None:
                raise HardwareAffinityError(f"Unknown model hint: {model_hint}")
            preferred = next(
                (t for t in _TIERS if spec.verdict_for_tier(t) == "PREFER"),
                next(
                    (t for t in _TIERS if spec.verdict_for_tier(t) == "ALLOW"),
                    None,
                ),
            )
            if preferred is None:
                raise HardwareAffinityError(
                    f"Model '{model_hint}' has no available tier"
                )
            return HardwareDecision(
                provider=preferred,
                hardware_tier=preferred,
                model=model_hint,
                reason="explicit_model_hint",
            )

        route_key = f"{task_type}:{optimize_for}"
        model_name = (
            routing.get(route_key)
            or routing.get(f"{task_type}:default")
            or routing["default"]
        )
        spec = models[model_name]
        preferred = next(
            (t for t in _TIERS if spec.verdict_for_tier(t) == "PREFER"),
            next(
                (t for t in _TIERS if spec.verdict_for_tier(t) == "ALLOW"),
                "shared",
            ),
        )
        return HardwareDecision(
            provider=preferred,
            hardware_tier=preferred,
            model=model_name,
            reason=f"policy:{route_key}",
        )
