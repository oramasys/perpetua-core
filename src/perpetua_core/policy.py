from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import yaml

Verdict = Literal["ALLOW", "PREFER", "NEVER"]


class HardwareAffinityError(RuntimeError):
    """Pre-spawn hardware affinity gate failure."""


@dataclass(frozen=True)
class HardwareDecision:
    provider: str
    hardware_tier: str
    model: str
    reason: str


class HardwarePolicyResolver:
    def __init__(self, policy: dict):
        self._policy = policy

    @classmethod
    def from_file(cls, path: str | Path) -> "HardwarePolicyResolver":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def check_affinity(self, *, model: str, target_tier: str) -> Verdict:
        spec = self._policy["models"].get(model)
        if spec is None:
            return "ALLOW"
        verdict = spec.get(target_tier, "ALLOW")
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
        models = self._policy["models"]
        routing = self._policy["routing"]

        if model_hint:
            spec = models.get(model_hint)
            if spec is None:
                raise HardwareAffinityError(f"Unknown model hint: {model_hint}")
            # raise if the model is forbidden on all declared tiers
            for tier, verdict in spec.items():
                if tier in ("mac", "windows", "shared") and verdict == "NEVER":
                    continue
                if tier not in ("mac", "windows", "shared"):
                    continue
            # pick the preferred tier
            preferred = next(
                (t for t in ("mac", "windows", "shared") if spec.get(t) == "PREFER"),
                next(
                    (t for t in ("mac", "windows", "shared") if spec.get(t) == "ALLOW"),
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
            (t for t in ("mac", "windows", "shared") if spec.get(t) == "PREFER"),
            next(
                (t for t in ("mac", "windows", "shared") if spec.get(t) == "ALLOW"),
                "shared",
            ),
        )
        return HardwareDecision(
            provider=preferred,
            hardware_tier=preferred,
            model=model_name,
            reason=f"policy:{route_key}",
        )
