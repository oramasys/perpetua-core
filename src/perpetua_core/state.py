from __future__ import annotations
from copy import deepcopy
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

HardwareTier = Literal["mac", "windows", "shared"]
TaskType = Literal["coding", "reasoning", "research", "ops"]
OptHint = Literal["speed", "quality", "reasoning"]


class PerpetuaState(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    session_id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    scratchpad: dict[str, Any] = Field(default_factory=dict)
    status: Literal["idle", "running", "interrupted", "error", "done"] = "idle"
    error: str | None = None
    nodes_visited: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    target_tier: HardwareTier = "shared"
    task_type: TaskType = "reasoning"
    optimize_for: OptHint = "quality"
    model_hint: str | None = None

    def merge(self, delta: dict[str, Any]) -> "PerpetuaState":
        """Return an isolated next state with ``delta`` applied.

        Both copy layers are load-bearing. ``deep=True`` isolates nested mutable
        values inherited from the current model. ``deepcopy(delta)`` separately
        isolates mutable values supplied by the caller because Pydantic applies
        ``update`` values after copying the model and otherwise keeps those
        update references directly.

        Nodes and observers must still treat the state they receive as input,
        not as a mutable workspace. ``merge`` guarantees isolation between the
        prior state, caller-owned delta values, and the returned generation; it
        does not make Python containers intrinsically frozen.
        """
        return self.model_copy(update=deepcopy(delta), deep=True)
