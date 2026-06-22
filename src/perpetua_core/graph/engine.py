"""
MiniGraph — ~80-line state-machine kernel with cycle guard.

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
_DEFAULT_MAX_STEPS = 200  # generous; cycles still trip well before runaway

NodeFn = Callable[[PerpetuaState], object]
EdgeFn = Callable[[PerpetuaState], str]


class MaxStepsExceeded(RuntimeError):
    def __init__(self, steps: int, last_node: str):
        super().__init__(f"MiniGraph exceeded max_steps={steps} (last node: {last_node})")
        self.steps = steps
        self.last_node = last_node


class MiniGraph:
    def __init__(self, *, interrupt_handler: str | None = None,
                 max_steps: int = _DEFAULT_MAX_STEPS):
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str | EdgeFn] = {}
        self._interrupt_handler = interrupt_handler
        self._max_steps = max_steps

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

        steps = 0
        last = node
        while node and node != END:
            steps += 1
            if steps > self._max_steps:
                raise MaxStepsExceeded(self._max_steps, last)
            last = node
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

    def set_entry(self, node: str) -> "MiniGraph":
        return self.add_edge(START, node)

    def compile(self) -> "CompiledGraph":
        return CompiledGraph(dict(self._nodes), dict(self._edges),
                             self._interrupt_handler, self._max_steps)


def _is_interrupt(exc: Exception) -> bool:
    return type(exc).__name__ == "Interrupt" and hasattr(exc, "prompt")


class CompiledGraph:
    """Frozen MiniGraph. Mutation methods are not present."""
    def __init__(self, nodes: dict, edges: dict, interrupt_handler: str | None, max_steps: int):
        # Reuse MiniGraph.ainvoke logic by constructing an internal graph.
        self._inner = MiniGraph(interrupt_handler=interrupt_handler, max_steps=max_steps)
        for n, fn in nodes.items():
            self._inner.add_node(n, fn)
        for src, dst in edges.items():
            self._inner.add_edge(src, dst)

    async def ainvoke(self, state: PerpetuaState) -> PerpetuaState:
        return await self._inner.ainvoke(state)
