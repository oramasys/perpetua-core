"""
MiniGraph — ~70-line state-machine kernel.

Nodes: async callables (state) -> dict
Edges: str (static) or callable (state) -> str (conditional)
No optional dependencies. No plugin imports.
"""
from __future__ import annotations
import asyncio
from typing import Callable

from perpetua_core.state import PerpetuaState

START = "__start__"
END = "__end__"

NodeFn = Callable[[PerpetuaState], object]  # returns Awaitable[dict] | dict
EdgeFn = Callable[[PerpetuaState], str]


class MiniGraph:
    def __init__(self, *, interrupt_handler: str | None = None):
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str | EdgeFn] = {}
        self._interrupt_handler = interrupt_handler

    def add_node(self, name: str, fn: NodeFn) -> "MiniGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str | EdgeFn) -> "MiniGraph":
        self._edges[src] = dst
        return self

    async def ainvoke(self, state: PerpetuaState) -> PerpetuaState:
        node = self._edges.get(START, END)
        if callable(node):
            node = node(state)

        while node and node != END:
            state = state.merge({"nodes_visited": [*state.nodes_visited, node]})
            fn = self._nodes[node]
            try:
                delta = await fn(state) if asyncio.iscoroutinefunction(fn) else fn(state)
            except Exception as exc:
                if _is_interrupt(exc):
                    return state.merge({
                        "status": "interrupted",
                        "metadata": {
                            **state.metadata,
                            "interrupt_prompt": exc.prompt,       # type: ignore[attr-defined]
                            "interrupt_payload": exc.payload,     # type: ignore[attr-defined]
                            "interrupt_node": node,
                        },
                    })
                raise
            state = state.merge(delta)
            edge = self._edges.get(node, END)
            node = edge(state) if callable(edge) else edge

        return state.merge({"status": "done"})


def _is_interrupt(exc: Exception) -> bool:
    return type(exc).__name__ == "Interrupt" and hasattr(exc, "prompt")
