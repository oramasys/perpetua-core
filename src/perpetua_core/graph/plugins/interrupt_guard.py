"""resume_policy — apply MERGE or DROP semantics to interrupt resume values."""
from __future__ import annotations
from enum import Enum
from perpetua_core.state import PerpetuaState


class ResumeMode(str, Enum):
    MERGE = "merge"      # resume value is dict, merged into scratchpad
    DROP = "drop"        # resume value replaces ONE specified key only


def resume_policy(
    state: PerpetuaState,
    resume_value: dict,
    *,
    mode: ResumeMode,
    drop_key: str | None = None,
) -> PerpetuaState:
    if mode is ResumeMode.MERGE:
        return state.merge({"scratchpad": {**state.scratchpad, **resume_value}})
    if mode is ResumeMode.DROP:
        if not drop_key:
            raise ValueError("DROP mode requires drop_key")
        if drop_key not in resume_value:
            raise ValueError(f"resume_value lacks drop_key={drop_key!r}")
        return state.merge({"scratchpad": {**state.scratchpad, drop_key: resume_value[drop_key]}})
    raise ValueError(f"unknown ResumeMode: {mode}")
