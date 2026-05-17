"""parallel_dispatch — fan-out N node-fns concurrently, merge deltas in order."""
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable
from perpetua_core.state import PerpetuaState

NodeFn = Callable[[PerpetuaState], Awaitable[dict] | dict]


async def parallel_dispatch(
    branches: list[tuple[str, NodeFn]],
    state: PerpetuaState,
) -> dict:
    """Run all branches concurrently. Returns merged delta dict.
    Merge rule: last branch in the list wins on key conflict (caller orders intent)."""
    async def _run(fn):
        out = fn(state)
        return await out if asyncio.iscoroutine(out) else out

    deltas = await asyncio.gather(*(_run(fn) for _, fn in branches))

    merged: dict = {}
    for d in deltas:
        if not d:
            continue
        for k, v in d.items():
            if k == "scratchpad" and isinstance(v, dict):
                merged["scratchpad"] = {**(merged.get("scratchpad") or {}), **v}
            else:
                merged[k] = v
    return merged
