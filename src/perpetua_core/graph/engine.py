"""MiniGraph — typed, bounded state-machine kernel.

The kernel owns only universal graph execution mechanics: named nodes, static
or conditional edges, single-level fan-out regions with reducers and joins,
START/END sentinels, bounded traversal, structural interrupts, detached
compilation, and structural execution observations.

GraphSpec description/linting is declarative and never becomes a second
scheduler. A fan-out region runs inside the same ``_run`` loop. Persistence,
retries, provider policy, telemetry export, and graph optimization remain
outside this module.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

from perpetua_core.graph.reducers import DEFAULT_REDUCER, Reducer, reduce_field
from perpetua_core.graph.spec import (
    JOIN_KINDS,
    EdgeSpec,
    GraphSpec,
    JoinKind,
    JoinSpec,
    NodeSpec,
    ReducerSpec,
    stable_callable_ref,
)
from perpetua_core.state import PerpetuaState

START = "__start__"
END = "__end__"
_DEFAULT_MAX_STEPS = 200

NodeDelta: TypeAlias = dict[str, Any]
NodeResult: TypeAlias = NodeDelta | Awaitable[NodeDelta]
NodeFn: TypeAlias = Callable[[PerpetuaState], NodeResult]
EdgeFn: TypeAlias = Callable[[PerpetuaState], str]


@dataclass(frozen=True, slots=True)
class ConditionalEdge:
    """Conditional routing with optional declarative target metadata.

    ``router`` remains the runtime callable used by the canonical scheduler.
    ``declared_targets`` is structural metadata only and lets GraphSpec/static
    validation reason more precisely without executing the router.
    """

    router: EdgeFn
    declared_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "declared_targets",
            tuple(sorted(set(self.declared_targets))),
        )


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    """What a custom join sees about one settled branch; never its delta."""

    name: str
    ok: bool
    error_type: str | None = None


JoinAdmitFn: TypeAlias = Callable[[tuple[BranchOutcome, ...]], Iterable[str]]


@dataclass(frozen=True, slots=True)
class Join:
    """Which settled branches of a region are admitted.

    Every branch settles before the join is evaluated, so a join decides
    admission and failure, never timing.
    """

    kind: JoinKind = "all"
    quorum: int | None = None
    fn: JoinAdmitFn | None = None

    def __post_init__(self) -> None:
        if self.kind not in JOIN_KINDS:
            raise ValueError(f"unsupported join kind: {self.kind!r}")
        if self.kind == "quorum":
            if isinstance(self.quorum, bool) or not isinstance(self.quorum, int) or self.quorum < 1:
                raise ValueError("quorum join requires an integer quorum >= 1")
        elif self.quorum is not None:
            raise ValueError("only a quorum join may set quorum")
        if self.kind == "custom" and not callable(self.fn):
            raise ValueError("custom join requires a callable fn")
        if self.kind != "custom" and self.fn is not None:
            raise ValueError("only a custom join may set fn")


@dataclass(frozen=True, slots=True)
class FanOut:
    """A diamond region: run ``branches`` concurrently, then continue at ``then``.

    Branch order is canonical (ascending name), so authoring order never
    affects results or ``graph_id``. Branch nodes must not declare their own
    outgoing edge; a region ignores it.
    """

    branches: tuple[str, ...]
    then: str
    join: Join = field(default_factory=Join)

    def __post_init__(self) -> None:
        branches = tuple(self.branches)
        if len(branches) < 2:
            raise ValueError("FanOut requires at least two branches")
        if len(set(branches)) != len(branches):
            raise ValueError("FanOut branches must be unique")
        if not all(isinstance(b, str) and b for b in branches):
            raise ValueError("FanOut branch names must be non-empty strings")
        if not isinstance(self.then, str) or not self.then:
            raise ValueError("FanOut.then must be a node name or END")
        if self.join.kind == "quorum" and self.join.quorum > len(branches):  # type: ignore[operator]
            raise ValueError("quorum cannot exceed the number of branches")
        object.__setattr__(self, "branches", tuple(sorted(branches)))


Edge: TypeAlias = str | EdgeFn | ConditionalEdge | FanOut
EventKind: TypeAlias = Literal[
    "node.start",
    "node.end",
    "edge.selected",
    "interrupt",
    "done",
    "superstep.start",
    "superstep.commit",
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
    branches: tuple[str, ...] = ()


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
    # ``superstep.commit`` only: state field -> branches that supplied it.
    provenance: Mapping[str, tuple[str, ...]] | None = None


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


def _describe_topology(
    nodes: Mapping[str, NodeFn],
    edges: Mapping[str, Edge],
    max_steps: int,
    reducers: Mapping[str, Reducer] | None = None,
) -> GraphSpec:
    node_specs = tuple(
        NodeSpec(name=name, implementation_ref=stable_callable_ref(fn))
        for name, fn in nodes.items()
    )
    edge_specs: list[EdgeSpec] = []
    join_specs: list[JoinSpec] = []
    for source, edge in edges.items():
        if isinstance(edge, str):
            edge_specs.append(EdgeSpec(source=source, kind="static", target=edge))
        elif isinstance(edge, FanOut):
            edge_specs.append(
                EdgeSpec(
                    source=source,
                    kind="fanout",
                    target=edge.then,
                    declared_targets=edge.branches,
                )
            )
            join_specs.append(
                JoinSpec(
                    source=source,
                    kind=edge.join.kind,
                    quorum=edge.join.quorum,
                    ref=stable_callable_ref(edge.join.fn) if edge.join.fn else None,
                )
            )
        elif isinstance(edge, ConditionalEdge):
            edge_specs.append(
                EdgeSpec(
                    source=source,
                    kind="conditional",
                    router_ref=stable_callable_ref(edge.router),
                    declared_targets=edge.declared_targets,
                )
            )
        else:
            edge_specs.append(
                EdgeSpec(
                    source=source,
                    kind="conditional",
                    router_ref=stable_callable_ref(edge),
                )
            )
    reducer_specs = tuple(
        ReducerSpec(
            field=name,
            kind=reducer.kind,
            ref=stable_callable_ref(reducer.fn) if reducer.fn else None,
        )
        for name, reducer in (reducers or {}).items()
    )
    return GraphSpec.create(
        max_steps=max_steps,
        nodes=node_specs,
        edges=tuple(edge_specs),
        reducers=reducer_specs,
        joins=tuple(join_specs),
    )


@dataclass(slots=True)
class _Settled:
    name: str
    delta: NodeDelta | None
    error: Exception | None


@dataclass(slots=True)
class _RegionResult:
    state: PerpetuaState
    steps: int
    interrupted: bool = False


class CompiledGraph:
    """Detached execution snapshot of a :class:`MiniGraph` topology."""

    def __init__(
        self,
        nodes: dict[str, NodeFn],
        edges: dict[str, Edge],
        max_steps: int,
        reducers: Mapping[str, Reducer] | None = None,
    ) -> None:
        self._nodes = dict(nodes)
        self._edges = dict(edges)
        self._max_steps = max_steps
        self._reducers = dict(reducers or {})

    @property
    def nodes(self) -> Mapping[str, NodeFn]:
        """Read-only view of the compiled topology's nodes."""
        return MappingProxyType(self._nodes)

    @property
    def edges(self) -> Mapping[str, Edge]:
        """Read-only view of the compiled topology's edges.

        Each value is a static target name, a bare conditional callable, or a
        :class:`ConditionalEdge` carrying the same runtime router plus optional
        declarative target metadata.
        """
        return MappingProxyType(self._edges)

    @property
    def reducers(self) -> Mapping[str, Reducer]:
        """Read-only view of the declared per-field reducers."""
        return MappingProxyType(self._reducers)

    def describe(self) -> GraphSpec:
        """Return an immutable structural description without executing code."""
        return _describe_topology(
            self._nodes, self._edges, self._max_steps, self._reducers
        )

    def validate(self):
        """Return the static GraphSpec validation report for this snapshot."""
        from perpetua_core.graph.lint import lint_graph_spec

        return lint_graph_spec(self.describe())

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

            edge = self._edges.get(current_node, END)
            if isinstance(edge, FanOut):
                result = _RegionResult(state, steps)
                async for observation in self._run_region(
                    current_node, edge, state, steps, result
                ):
                    yield observation
                if result.interrupted:
                    return
                state, steps = result.state, result.steps
                node = self._resolve_edge(edge.then, state)
            else:
                node = self._resolve_edge(edge, state)
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

    async def _run_region(
        self,
        source: str,
        edge: FanOut,
        state: PerpetuaState,
        steps: int,
        result: _RegionResult,
    ) -> AsyncIterator[GraphObservation]:
        """Run one fan-out region inside the canonical scheduler loop.

        All branches see one snapshot, all settle, and one atomic commit folds
        the admitted deltas in ascending branch-name order. Nothing is
        committed when an interrupt, a join refusal or a reducer conflict ends
        the region.
        """
        branches = edge.branches
        if steps + len(branches) > self._max_steps:
            raise MaxStepsExceeded(steps=steps, last_node=source)
        for name in branches:
            self._node_for(name)
        if edge.then != END and edge.then not in self._nodes:
            raise ValueError(f"MiniGraph fan-out resolved to unknown node {edge.then!r}")

        snapshot = state.merge({"nodes_visited": [*state.nodes_visited, *branches]})
        yield GraphObservation(
            GraphEvent(
                "superstep.start",
                node=source,
                target=edge.then,
                steps=steps,
                branches=branches,
            ),
            snapshot,
        )
        for name in branches:
            yield GraphObservation(GraphEvent("node.start", node=name, steps=steps), snapshot)

        settled = await self._settle(branches, snapshot)

        interrupted = [s for s in settled if s.error is not None and _is_interrupt(s.error)]
        if interrupted:
            first = interrupted[0]
            assert first.error is not None
            result.state = _interrupted_state(snapshot, first.name, first.error)
            result.interrupted = True
            yield GraphObservation(
                GraphEvent(
                    "interrupt",
                    node=first.name,
                    steps=steps,
                    terminal_reason="interrupted",
                    branches=branches,
                ),
                result.state,
            )
            return

        admitted = self._admit(source, edge.join, settled)
        merged, provenance = self._fold(snapshot, admitted)
        committed = snapshot.merge(merged)
        steps += sum(1 for s in settled if s.error is None)

        for item in admitted:
            yield GraphObservation(
                GraphEvent("node.end", node=item.name, steps=steps),
                committed,
                delta=item.delta,
            )
        yield GraphObservation(
            GraphEvent(
                "superstep.commit",
                node=source,
                target=edge.then,
                steps=steps,
                branches=tuple(item.name for item in admitted),
            ),
            committed,
            delta=merged,
            provenance=provenance,
        )
        result.state = committed
        result.steps = steps

    async def _settle(
        self, branches: tuple[str, ...], snapshot: PerpetuaState
    ) -> list[_Settled]:
        """Run every branch on one snapshot; return outcomes in branch order."""

        async def call(name: str) -> _Settled:
            try:
                # Isolated copy: a branch mutating its input cannot leak into a
                # sibling, the interrupted state or the committed state.
                delta = self._nodes[name](snapshot.model_copy(deep=True))
                if inspect.isawaitable(delta):
                    delta = await delta
            except Exception as exc:
                return _Settled(name, None, exc)
            if not isinstance(delta, dict):
                return _Settled(
                    name,
                    None,
                    TypeError(
                        f"MiniGraph node {name!r} returned "
                        f"{type(delta).__name__}; expected dict delta"
                    ),
                )
            return _Settled(name, delta, None)

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(call(name)) for name in branches]
        return [task.result() for task in tasks]

    def _admit(
        self, source: str, join: Join, settled: list[_Settled]
    ) -> list[_Settled]:
        """Apply the join; return admitted branches in canonical order."""
        ok = [s for s in settled if s.error is None]
        failed = [s for s in settled if s.error is not None]
        kind = join.kind
        if kind == "all":
            admitted = ok if not failed else None
        elif kind == "any":
            admitted = ok if ok else None
        elif kind == "first_success":
            admitted = ok[:1] if ok else None
        elif kind == "quorum":
            assert join.quorum is not None
            admitted = ok if len(ok) >= join.quorum else None
        else:
            assert join.fn is not None
            outcomes = tuple(
                BranchOutcome(s.name, s.error is None, type(s.error).__name__ if s.error else None)
                for s in settled
            )
            names = list(join.fn(outcomes))
            by_name = {s.name: s for s in settled}
            for name in names:
                if name not in by_name or by_name[name].error is not None:
                    raise ValueError(
                        f"custom join for {source!r} admitted {name!r}, "
                        "which is not a successful branch"
                    )
            chosen = set(names)
            admitted = [s for s in ok if s.name in chosen] or None
        if admitted is None:
            errors = [s.error for s in failed if s.error is not None]
            if not errors:
                errors = [ValueError(f"{kind} join admitted no branch")]
            raise ExceptionGroup(
                f"fan-out region {source!r} refused by {kind} join", errors
            )
        return admitted

    def _fold(
        self, snapshot: PerpetuaState, admitted: list[_Settled]
    ) -> tuple[NodeDelta, dict[str, tuple[str, ...]]]:
        """Fold admitted deltas, in branch order, into one delta plus provenance."""
        contributions: dict[str, list[tuple[str, Any]]] = {}
        for item in admitted:
            assert item.delta is not None
            for key, value in item.delta.items():
                contributions.setdefault(key, []).append((item.name, value))
        merged: NodeDelta = {}
        provenance: dict[str, tuple[str, ...]] = {}
        for key in sorted(contributions):
            declared = self._reducers.get(key)
            merged[key] = reduce_field(
                key,
                declared or DEFAULT_REDUCER,
                getattr(snapshot, key, None),
                contributions[key],
                contribution_semantics=declared is not None,
            )
            provenance[key] = tuple(name for name, _ in contributions[key])
        return merged, provenance

    def _node_for(self, name: str) -> NodeFn:
        try:
            return self._nodes[name]
        except KeyError:
            raise KeyError(
                f"MiniGraph has no node registered as {name!r}"
            ) from None

    def _resolve_edge(self, edge: Edge, state: PerpetuaState) -> str:
        if isinstance(edge, ConditionalEdge):
            target = edge.router(state)
        elif callable(edge):
            target = edge(state)
        else:
            target = edge
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
        self._reducers: dict[str, Reducer] = {}
        self._max_steps = max_steps

    def add_node(self, name: str, fn: NodeFn) -> "MiniGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: Edge) -> "MiniGraph":
        if isinstance(dst, FanOut) and src == START:
            raise ValueError("a fan-out region needs a source node; START cannot fan out")
        self._edges[src] = dst
        return self

    def add_reducer(
        self,
        field_name: str,
        kind: str | Reducer = "reject_conflict",
        fn: Callable[[Any, tuple[Any, ...]], Any] | None = None,
    ) -> "MiniGraph":
        """Declare how branch writes to ``field_name`` fold in a fan-out region."""
        reducer = kind if isinstance(kind, Reducer) else Reducer(kind, fn)  # type: ignore[arg-type]
        self._reducers[field_name] = reducer
        return self

    def set_entry(self, node: str) -> "MiniGraph":
        return self.add_edge(START, node)

    def compile(self) -> CompiledGraph:
        return CompiledGraph(
            self._nodes, self._edges, self._max_steps, self._reducers
        )

    def compile_validated(self) -> CompiledGraph:
        """Compile and fail closed if the detached GraphSpec is invalid."""
        from perpetua_core.graph.lint import validate_graph_spec

        compiled = self.compile()
        validate_graph_spec(compiled.describe())
        return compiled

    def describe(self) -> GraphSpec:
        """Return an immutable structural description without executing code."""
        return _describe_topology(
            self._nodes, self._edges, self._max_steps, self._reducers
        )

    def validate(self):
        """Return the static GraphSpec validation report for this builder."""
        from perpetua_core.graph.lint import lint_graph_spec

        return lint_graph_spec(self.describe())

    @property
    def nodes(self) -> Mapping[str, NodeFn]:
        """Read-only view of the builder's nodes so far. See CompiledGraph.nodes."""
        return MappingProxyType(self._nodes)

    @property
    def edges(self) -> Mapping[str, Edge]:
        """Read-only view of the builder's edges so far. See CompiledGraph.edges."""
        return MappingProxyType(self._edges)

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
