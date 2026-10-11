"""Optional durable-start markers at Core callable boundaries."""
from __future__ import annotations

from typing import Any

import pytest

from perpetua_core.graph.engine import END, ConditionalEdge, FanOut, Join, MiniGraph
from perpetua_core.graph.gate import (
    CommitRequest,
    DispatchRequest,
    GateDecision,
    GateRefused,
)
from perpetua_core.graph.reducers import Reducer
from perpetua_core.state import PerpetuaState


def fresh() -> PerpetuaState:
    """Return a fresh graph state."""
    return PerpetuaState(session_id="start-gate")


class LegacyGate:
    """The mandatory T1 gate surface, without the optional start callback."""

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        return GateDecision.allow()

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        return GateDecision.allow()


class StartGate(LegacyGate):
    """Record durable-start requests and their ordering around callables."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.started: list[DispatchRequest] = []
        self.dispatched: list[DispatchRequest] = []

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        self.dispatched.append(request)
        self.events.append(f"dispatch:{request.boundary}:{','.join(request.nodes)}")
        return GateDecision.allow()

    async def before_start(self, request: DispatchRequest) -> None:
        self.started.append(request)
        self.events.append(f"start:{request.boundary}:{','.join(request.nodes)}")


async def test_legacy_gate_without_before_start_remains_compatible() -> None:
    calls: list[str] = []
    graph = MiniGraph()
    graph.add_node("work", lambda state: calls.append("work") or {})
    graph.set_entry("work")
    graph.add_edge("work", END)

    state = await graph.compile().ainvoke(fresh(), gate=LegacyGate())

    assert calls == ["work"]
    assert state.status == "done"


async def test_nodes_and_callable_routers_mark_start_after_admission() -> None:
    events: list[str] = []
    gate = StartGate(events)
    graph = MiniGraph()

    def route(state: PerpetuaState) -> str:
        events.append("call:router")
        return "work"

    def work(state: PerpetuaState) -> dict[str, Any]:
        events.append("call:node")
        return {}

    graph.add_node("work", work)
    graph.add_edge("__start__", ConditionalEdge(route, ("work",)))
    graph.add_edge("work", END)

    await graph.compile().ainvoke(fresh(), gate=gate)

    assert events == [
        "dispatch:router:__start__",
        "start:router:__start__",
        "call:router",
        "dispatch:node:work",
        "start:node:work",
        "call:node",
    ]
    assert gate.started[0] is gate.dispatched[0]
    assert gate.started[1] is gate.dispatched[1]


async def test_start_callback_failure_prevents_callable_control_transfer() -> None:
    calls: list[str] = []

    class BrokenStart(LegacyGate):
        async def before_start(self, request: DispatchRequest) -> None:
            raise RuntimeError("start ledger down")

    graph = MiniGraph()
    graph.add_node("work", lambda state: calls.append("work") or {})
    graph.set_entry("work")
    graph.add_edge("work", END)

    with pytest.raises(RuntimeError, match="start ledger down"):
        await graph.compile().ainvoke(fresh(), gate=BrokenStart())

    assert calls == []


async def test_fanout_branches_get_individual_correlated_start_requests() -> None:
    calls: list[str] = []
    gate = StartGate()
    graph = MiniGraph()
    graph.add_node("plan", lambda state: {})
    graph.add_node("a", lambda state: calls.append("a") or {"scratchpad": {"ok": True}})
    graph.add_node("b", lambda state: calls.append("b") or {"scratchpad": {"ok": True}})
    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(("b", "a"), END))

    await graph.compile().ainvoke(fresh(), gate=gate)

    fanout = next(request for request in gate.dispatched if request.boundary == "fanout")
    branch_starts = [
        request
        for request in gate.started
        if request.boundary == "node" and request.nodes in {("a",), ("b",)}
    ]
    assert calls == ["a", "b"]
    assert [(request.nodes, request.steps) for request in branch_starts] == [
        (("a",), fanout.steps),
        (("b",), fanout.steps),
    ]
    assert all(request.source == fanout.source == "plan" for request in branch_starts)
    assert all(request.batch_id == fanout.batch_id for request in branch_starts)
    assert all(
        any(request is dispatched for dispatched in gate.dispatched)
        for request in branch_starts
    )


async def test_fanout_start_failure_is_captured_as_that_branch_outcome() -> None:
    calls: list[str] = []

    class FailOneBranch(StartGate):
        async def before_start(self, request: DispatchRequest) -> None:
            await super().before_start(request)
            if request.boundary == "node" and request.nodes == ("a",):
                raise RuntimeError("a did not durably start")

    graph = MiniGraph()
    graph.add_node("plan", lambda state: {})
    graph.add_node("a", lambda state: calls.append("a") or {"scratchpad": {"a": True}})
    graph.add_node("b", lambda state: calls.append("b") or {"scratchpad": {"b": True}})
    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(("a", "b"), END, Join("any")))

    observations = []
    with pytest.raises(ExceptionGroup, match="durable start"):
        async for observation in graph.compile().aobserve(
            fresh(), gate=FailOneBranch()
        ):
            observations.append(observation)

    assert calls == ["b"]
    assert "superstep.commit" not in [item.event.kind for item in observations]


async def test_fanout_branch_dispatch_refusal_aborts_after_other_branches_settle() -> None:
    calls: list[str] = []

    class RefuseOneBranch(StartGate):
        async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
            await super().before_dispatch(request)
            if (
                request.boundary == "node"
                and request.nodes == ("a",)
                and request.batch_id is not None
            ):
                return GateDecision.refuse("branch authority expired")
            return GateDecision.allow()

    graph = MiniGraph()
    graph.add_node("plan", lambda state: {})
    graph.add_node("a", lambda state: calls.append("a") or {})
    graph.add_node("b", lambda state: calls.append("b") or {"scratchpad": {"b": True}})
    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(("a", "b"), END, Join("any")))

    observations = []
    with pytest.raises(GateRefused) as refused:
        async for observation in graph.compile().aobserve(
            fresh(), gate=RefuseOneBranch()
        ):
            observations.append(observation)

    assert refused.value.request.boundary == "node"
    assert refused.value.request.nodes == ("a",)
    assert calls == ["b"]
    assert "superstep.commit" not in [item.event.kind for item in observations]


async def test_custom_join_and_each_custom_reducer_mark_only_real_calls() -> None:
    events: list[str] = []
    gate = StartGate(events)
    graph = MiniGraph()
    graph.add_node("plan", lambda state: {})
    graph.add_node(
        "a",
        lambda state: {
            "messages": [{"a": 1}],
            "metadata": {"a": 1},
            "scratchpad": {"a": 1},
        },
    )
    graph.add_node(
        "b",
        lambda state: {
            "messages": [{"b": 1}],
            "metadata": {"b": 1},
            "scratchpad": {"b": 1},
        },
    )

    def admit(outcomes):
        events.append("call:join")
        return [outcome.name for outcome in outcomes if outcome.ok]

    def fold(base: Any, values: tuple[Any, ...]) -> dict[str, Any]:
        events.append("call:reducer")
        merged = dict(base or {})
        for value in values:
            merged.update(value)
        return merged

    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(("a", "b"), END, Join("custom", fn=admit)))
    graph.add_reducer("messages", Reducer("concat"))
    graph.add_reducer("metadata", Reducer("custom", fold))
    graph.add_reducer("scratchpad", Reducer("custom", fold))

    await graph.compile().ainvoke(fresh(), gate=gate)

    join_start = next(request for request in gate.started if request.boundary == "join")
    reducer_starts = [request for request in gate.started if request.boundary == "reducer"]
    fanout = next(request for request in gate.dispatched if request.boundary == "fanout")
    reducer_dispatches = [
        request for request in gate.dispatched if request.boundary == "reducer"
    ]
    assert events.index("start:join:a,b") < events.index("call:join")
    assert events.index("start:reducer:a,b,metadata") < events.index("call:reducer")
    assert len(reducer_starts) == 2
    assert [request.nodes for request in reducer_starts] == [
        ("a", "b", "metadata"),
        ("a", "b", "scratchpad"),
    ]
    assert len(set(reducer_starts)) == 2
    assert [request.nodes for request in reducer_dispatches] == [
        ("a", "b"),
        ("a", "b", "metadata"),
        ("a", "b", "scratchpad"),
    ]
    assert all(
        any(request is dispatched for dispatched in gate.dispatched)
        for request in gate.started
    )
    assert events.count("call:reducer") == 2
    assert all(request.source == join_start.source == "plan" for request in reducer_starts)
    assert all(request.steps == join_start.steps == 3 for request in reducer_starts)
    assert join_start.batch_id == fanout.batch_id
    assert all(request.batch_id == fanout.batch_id for request in reducer_starts)
