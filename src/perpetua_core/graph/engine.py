"""MiniGraph — typed, bounded state-machine kernel.

The kernel owns only universal graph execution mechanics: named nodes, static
or conditional edges, START/END sentinels, bounded traversal, structural
interrupts, detached compilation, and structural execution observations.

Persistence, retries, reducers, provider policy, telemetry export, and graph
optimization remain outside this module.
"""
from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from perpetua_core.state import PerpetuaState

START = "__start__"
END = "__end__"
_DEFAULT_MAX_STEPS = 200

NodeDelta: TypeAlias = dict[str, Any]
NodeResult: TypeAlias = NodeDelta | Awaitable[NodeDelta]
NodeFn: TypeAlias = Callable[[PerpetuaState], NodeResult]
EdgeFn: TypeAlias = Callable[[PerpetuaState], str]
Edge: TypeAlias = str | EdgeFn
EventKind: TypeAlias = Literal[
    "node.start", "node.end", "edge.selected", "interrupt", "done"
]
TerminalReason: TypeAlias = Literal["done", "interrupted"]


@dataclass(frozen=True, slots=True)
class GraphEvent:
    """Sanitized structural execution event.

    ``GraphEvent`` is the control-plane projection intended for streaming,
    API/UI consumers, and other observers that do not need graph state.
    Rich state and node deltas live on :class:`GraphObservation` instead.
    """

    kind: EventKind
    node: str | None = None
    target: str | None = None
    steps: int = 0
    terminal_reason: TerminalReason | None = None


@dataclass(frozen=True, slots=True)
class GraphObservation:
    """Rich in-process observation emitted by the canonical scheduler.

    The observation seam exists for trusted in-process adapters such as
    checkpointers, tracers, audit hooks, and plugin dispatchers. ``delta`` is
    present for ``node.end`` observations and ``None`` for other event kinds.
    """

    event: GraphEvent
    state: PerpetuaState
    delta: NodeDelta | None = None


class MaxStepsExceeded(RuntimeError):
    """Raised before a node execution would exceed ``max_steps``.

    ``steps`` is the number of completed node executions. ``last_node`` is the
    most recently entered node, or START when the configured budget is zero.
    """

    def __init__(self, steps: int, last_node: str) -> None:
        self.steps = steps
        self.last_node = last_node
        super().__init__(
            f"MiniGraph exceeded max_steps after {steps} completed steps "
            f"(last node: {last_node!r})"
        )


class CompiledGraph:
    """Detached execution snapshot of a :class:`MiniGraph` topology."""

    def __init__(
        self,
        nodes: dict[str, NodeFn],
        edges: dict[str, Edge],
        max_steps: int,
    ) -> None:
        self._nodes = dict(nodes)
        self._edges = dict(edges)
        self._max_steps = max_steps

    async def ainvoke(self, state: PerpetuaState) -> PerpetuaState:
        """Run the graph to normal completion or structural interruption."""
        final_state = state
        async for observation in self.aobserve(state):
            final_state = observation.state
        return final_state

    async def aobserve(
        self,
        state: PerpetuaState,
    ) -> AsyncIterator[GraphObservation]:
        """Yield rich in-process observations from the canonical scheduler."""
        async for observation in self._run(state):
            yield observation

    async def asteps(self, state: PerpetuaState) -> AsyncIterator[GraphEvent]:
        """Yield sanitized structural events from the canonical scheduler."""
        async for observation in self.aobserve(state):
            yield observation.event

    async def _run(self, state: PerpetuaState) -> AsyncIterator[GraphObservation]:
        """Sole graph scheduler; all execution views project from this loop."""
        node = self._resolve_edge(self._edges.get(START, END), state)
        steps = 0
        last_node = START

        yield GraphObservation(
            GraphEvent("edge.selected", node=START, target=node, steps=steps),
            state,
        )

        while node != END:
            if steps >= self._max_steps:
                raise MaxStepsExceeded(steps=steps, last_node=last_node)

            current_node = node
            node_fn = self._node_for(current_node)
            state = state.merge(
                {"nodes_visited": [*state.nodes_visited, current_node]}
            )
            last_node = current_node

            yield GraphObservation(
                GraphEvent("node.start", node=current_node, steps=steps),
                state,
            )

            try:
                delta = node_fn(state)
                if inspect.isawaitable(delta):
                    delta = await delta
            except Exception as exc:
                if _is_interrupt(exc):
                    state = _interrupted_state(state, current_node, exc)
                    yield GraphObservation(
                        GraphEvent(
                            "interrupt",
                            node=current_node,
                            steps=steps,
                            terminal_reason="interrupted",
                        ),
                        state,
                    )
                    return
                raise

            if not isinstance(delta, dict):
                raise TypeError(
                    f"MiniGraph node {current_node!r} returned "
                    f"{type(delta).__name__}; expected dict delta"
                )

            state = state.merge(delta)
            steps += 1
            yield GraphObservation(
                GraphEvent("node.end", node=current_node, steps=steps),
                state,
                delta=delta,
            )

            node = self._resolve_edge(self._edges.get(current_node, END), state)
            yield GraphObservation(
                GraphEvent(
                    "edge.selected",
                    node=current_node,
                    target=node,
                    steps=steps,
                ),
                state,
            )

        state = state.merge({"status": "done"})
        yield GraphObservation(
            GraphEvent("done", steps=steps, terminal_reason="done"),
            state,
        )

    def _node_for(self, name: str) -> NodeFn:
        try:
            return self._nodes[name]
        except KeyError:
            raise KeyError(
                f"MiniGraph has no node registered as {name!r}"
            ) from None

    def _resolve_edge(self, edge: Edge, state: PerpetuaState) -> str:
        target = edge(state) if callable(edge) else edge
        if not isinstance(target, str):
            raise TypeError(
                "MiniGraph edge resolved to "
                f"{type(target).__name__}; expected node name or END string"
            )
        if not target:
            raise ValueError("MiniGraph edge resolved to an empty node name")
        if target != END and target not in self._nodes:
            raise ValueError(
                f"MiniGraph edge resolved to unknown node {target!r}"
            )
        return target


class MiniGraph:
    """Mutable graph builder; ``compile()`` creates a detached runtime snapshot."""

    def __init__(self, *, max_steps: int = _DEFAULT_MAX_STEPS) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, Edge] = {}
        self._max_steps = max_steps

    def add_node(self, name: str, fn: NodeFn) -> "MiniGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: Edge) -> "MiniGraph":
        self._edges[src] = dst
        return self

    def set_entry(self, node: str) -> "MiniGraph":
        return self.add_edge(START, node)

    def compile(self) -> CompiledGraph:
        return CompiledGraph(self._nodes, self._edges, self._max_steps)

    async def ainvoke(self, state: PerpetuaState) -> PerpetuaState:
        return await self.compile().ainvoke(state)


def _is_interrupt(exc: Exception) -> bool:
    return type(exc).__name__ == "Interrupt" and hasattr(exc, "prompt")


def _interrupted_state(
    state: PerpetuaState,
    node: str,
    exc: Exception,
) -> PerpetuaState:
    return state.merge(
        {
            "status": "interrupted",
            "metadata": {
                **state.metadata,
                "interrupt_node": node,
                "interrupt_prompt": getattr(exc, "prompt"),
                "interrupt_payload": getattr(exc, "payload", None),
            },
        }
    )
