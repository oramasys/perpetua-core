"""Streaming plugin — AsyncGenerator wrapper over MiniGraph.ainvoke."""
from __future__ import annotations
from typing import AsyncGenerator
from perpetua_core.state import PerpetuaState

StreamEvent = tuple  # ("node", node_name, delta) | ("done", final_state)


async def astream(
    graph, initial_state: PerpetuaState
) -> AsyncGenerator[StreamEvent, None]:
    """Yields per-node events. Runs graph step-by-step; token streaming
    is delegated to LLMClient (OpenAI streaming API at that layer)."""
    import asyncio
    from perpetua_core.graph.engine import START, END

    state = initial_state
    node = graph._edges.get(START, END)
    if callable(node):
        node = node(state)

    while node and node != END:
        state = state.merge({"nodes_visited": [*state.nodes_visited, node]})
        fn = graph._nodes[node]
        delta = await fn(state) if asyncio.iscoroutinefunction(fn) else fn(state)
        state = state.merge(delta)
        yield ("node", node, delta)
        edge = graph._edges.get(node, END)
        node = edge(state) if callable(edge) else edge

    final = state.merge({"status": "done"})
    yield ("done", final)
