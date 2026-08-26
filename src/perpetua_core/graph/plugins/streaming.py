"""Structural streaming adapter over MiniGraph's canonical scheduler.

Token-level model streaming remains the LLM client's responsibility. This
adapter yields only the kernel's control-plane ``GraphEvent`` values and never
reimplements traversal through private ``_nodes``/``_edges`` state.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from perpetua_core.graph.engine import CompiledGraph, GraphEvent, MiniGraph
from perpetua_core.state import PerpetuaState


async def astream(
    graph: MiniGraph | CompiledGraph,
    initial_state: PerpetuaState,
) -> AsyncIterator[GraphEvent]:
    compiled = graph.compile() if isinstance(graph, MiniGraph) else graph
    async for event in compiled.asteps(initial_state):
        yield event
