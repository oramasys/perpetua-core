from __future__ import annotations
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
        return self.model_copy(update=delta)
