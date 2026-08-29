"""Multi-observer fan-out over MiniGraph's canonical observation seam.

This module never schedules graph nodes itself. It drains one
``CompiledGraph.aobserve()`` stream and pushes each observation to every
registered listener in deterministic registration order.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Sequence
from typing import Protocol

from perpetua_core.graph.engine import (
    CompiledGraph,
    GraphObservation,
    MiniGraph,
)
from perpetua_core.state import PerpetuaState


class GraphPlugin(Protocol):
    """Trusted in-process observer of graph execution."""

    def on_observation(
        self,
        observation: GraphObservation,
    ) -> object | Awaitable[object]: ...


async def run_with_plugins(
    graph: MiniGraph | CompiledGraph,
    initial_state: PerpetuaState,
    plugins: Sequence[GraphPlugin],
) -> PerpetuaState:
    """Run once and push every observation to every plugin.

    Delivery is ordered and fail-closed by default. A plugin failure therefore
    stops the run instead of silently losing checkpoint/audit evidence. Richer
    per-plugin failure policy can be layered above this primitive later.
    """
    compiled = graph.compile() if isinstance(graph, MiniGraph) else graph
    final_state = initial_state

    async for observation in compiled.aobserve(initial_state):
        final_state = observation.state
        for plugin in plugins:
            result = plugin.on_observation(observation)
            if inspect.isawaitable(result):
                await result

    return final_state
