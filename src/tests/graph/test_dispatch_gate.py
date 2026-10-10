"""T1: the neutral DispatchGate seam inside the one scheduler.

Core owns only the protocol and the call sites. Whoever implements the gate
(Oramasys) owns authority, leases, stop state, delivery health and budget.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from perpetua_core.graph.engine import END, ConditionalEdge, FanOut, Join, MiniGraph
from perpetua_core.graph.gate import (
    CommitRequest,
    DispatchRequest,
    GateDecision,
    GateRefused,
    GateStopped,
)
from perpetua_core.graph.reducers import Reducer
from perpetua_core.state import PerpetuaState


def fresh() -> PerpetuaState:
    return PerpetuaState(session_id="t1")


class RecordingGate:
    """Allows everything unless told otherwise; records every boundary."""

    def __init__(self, *, refuse: set[tuple[str, str]] | None = None,
                 stop: set[tuple[str, str]] | None = None) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.refuse = refuse or set()
        self.stop = stop or set()

    def _decide(self, phase: str, boundary: str) -> GateDecision:
        if (phase, boundary) in self.refuse:
            return GateDecision.refuse(f"{phase}.{boundary}.refused")
        if (phase, boundary) in self.stop:
            return GateDecision.stop(f"{phase}.{boundary}.stopped")
        return GateDecision.allow()

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        self.calls.append(("dispatch", request.boundary, request.nodes))
        return self._decide("dispatch", request.boundary)

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        self.calls.append(("commit", request.boundary, request.nodes))
        return self._decide("commit", request.boundary)


def linear(calls: list[str]) -> MiniGraph:
    graph = MiniGraph()

    def a(_: PerpetuaState) -> dict:
        calls.append("a")
        return {"scratchpad": {"a": 1}}

    graph.add_node("a", a)
    graph.set_entry("a")
    graph.add_edge("a", END)
    return graph


def region(calls: list[str], *, join: Join | None = None, reducer: bool = False) -> MiniGraph:
    graph = MiniGraph()
    graph.add_node("plan", lambda s: {})

    def branch(name: str):
        async def run(_: PerpetuaState) -> dict:
            calls.append(name)
            await asyncio.sleep(0)
            return {"messages": [{"m": name}]}

        return run

    graph.add_node("b1", branch("b1"))
    graph.add_node("b2", branch("b2"))
    graph.add_node("after", lambda s: {})
    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(("b1", "b2"), "after", join or Join()))
    graph.add_edge("after", END)
    if reducer:
        def fold(current: Any, values: tuple[Any, ...]) -> Any:
            calls.append("reducer")
            out = list(current or [])
            for value in values:
                out.extend(value)
            return out

        graph.add_reducer("messages", Reducer("custom", fold))
    else:
        graph.add_reducer("messages", Reducer("concat"))
    return graph


# --- no gate: unchanged behaviour ---------------------------------------


async def test_no_gate_keeps_existing_event_sequence():
    calls: list[str] = []
    plain = [o.event for o in [x async for x in linear(calls).compile().aobserve(fresh())]]
    gated = [o.event async for o in linear(calls).compile().aobserve(fresh(), gate=RecordingGate())]
    assert plain == gated


# --- every execution boundary is guarded ---------------------------------


async def test_linear_node_is_guarded_before_dispatch_and_before_commit():
    gate = RecordingGate()
    await linear([]).compile().ainvoke(fresh(), gate=gate)
    assert gate.calls == [("dispatch", "node", ("a",)), ("commit", "node", ("a",))]


async def test_router_is_guarded_before_it_runs():
    routed: list[str] = []
    graph = MiniGraph()
    graph.add_node("a", lambda s: {})

    def router(_: PerpetuaState) -> str:
        routed.append("router")
        return END

    graph.set_entry("a")
    graph.add_edge("a", ConditionalEdge(router, (END,)))
    gate = RecordingGate(refuse={("dispatch", "router")})
    with pytest.raises(GateRefused) as info:
        await graph.compile().ainvoke(fresh(), gate=gate)
    assert routed == []
    assert info.value.decision.reason == "dispatch.router.refused"


async def test_region_boundaries_in_order():
    gate = RecordingGate()
    await region([]).compile().ainvoke(fresh(), gate=gate)
    assert gate.calls == [
        ("dispatch", "node", ("plan",)),
        ("commit", "node", ("plan",)),
        ("dispatch", "fanout", ("b1", "b2")),
        ("dispatch", "reducer", ("b1", "b2")),
        ("commit", "region", ("b1", "b2")),
        ("dispatch", "node", ("after",)),
        ("commit", "node", ("after",)),
    ]


async def test_custom_join_is_guarded_before_it_runs():
    joined: list[str] = []

    def admit(outcomes):
        joined.append("join")
        return [o.name for o in outcomes if o.ok]

    gate = RecordingGate(refuse={("dispatch", "join")})
    with pytest.raises(GateRefused):
        await region([], join=Join("custom", fn=admit)).compile().ainvoke(fresh(), gate=gate)
    assert joined == []


# --- refusal and stop never commit ----------------------------------------


async def test_refused_dispatch_never_calls_the_node():
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "node")})
    with pytest.raises(GateRefused) as info:
        await linear(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []
    assert info.value.request.nodes == ("a",)


async def test_refused_commit_runs_the_node_but_publishes_nothing():
    calls: list[str] = []
    gate = RecordingGate(refuse={("commit", "node")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in linear(calls).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert calls == ["a"]  # the call happened; its effects cannot be undone
    assert "node.end" not in seen


async def test_refused_batch_dispatches_no_branch():
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "fanout")})
    with pytest.raises(GateRefused):
        await region(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []


async def test_refused_reducer_never_folds_and_never_commits():
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "reducer")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in region(calls, reducer=True).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert sorted(calls) == ["b1", "b2"]  # settle-all preserved
    assert "superstep.commit" not in seen


async def test_refused_region_commit_publishes_nothing():
    gate = RecordingGate(refuse={("commit", "region")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in region([]).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert "superstep.commit" not in seen


async def test_stop_ends_the_run_without_committing():
    calls: list[str] = []
    gate = RecordingGate(stop={("commit", "node")})
    observations = [o async for o in linear(calls).compile().aobserve(fresh(), gate=gate)]
    last = observations[-1]
    assert last.event.kind == "stopped"
    assert last.event.terminal_reason == "stopped"
    assert last.state.scratchpad == {}
    assert "node.end" not in [o.event.kind for o in observations]


async def test_stop_raises_from_ainvoke_only_when_asked():
    gate = RecordingGate(stop={("dispatch", "node")})
    state = await linear([]).compile().ainvoke(fresh(), gate=gate)
    assert state.status == "stopped"


# --- fail closed ----------------------------------------------------------


async def test_gate_error_propagates_and_nothing_commits():
    class Broken(RecordingGate):
        async def before_commit(self, request):
            raise RuntimeError("ledger down")

    calls: list[str] = []
    with pytest.raises(RuntimeError, match="ledger down"):
        await linear(calls).compile().ainvoke(fresh(), gate=Broken())


async def test_non_decision_result_is_refused():
    class Sloppy(RecordingGate):
        async def before_dispatch(self, request):
            return True  # not a GateDecision

    with pytest.raises(TypeError):
        await linear([]).compile().ainvoke(fresh(), gate=Sloppy())


def test_decision_constructors_validate_reasons():
    assert GateDecision.allow().verdict == "allow"
    with pytest.raises(ValueError):
        GateDecision.refuse("")
    with pytest.raises(ValueError):
        GateDecision.stop("")
    with pytest.raises(ValueError):
        GateDecision("allow", "unexpected")
    with pytest.raises(ValueError):
        GateDecision("maybe", None)  # type: ignore[arg-type]


async def test_miniGraph_ainvoke_forwards_the_gate():
    gate = RecordingGate()
    await linear([]).ainvoke(fresh(), gate=gate)
    assert gate.calls[0] == ("dispatch", "node", ("a",))


async def test_gate_stopped_exported_for_callers():
    assert issubclass(GateStopped, RuntimeError)


async def test_stop_inside_a_region_commits_nothing_and_ends_the_run():
    calls: list[str] = []
    gate = RecordingGate(stop={("commit", "region")})
    observations = [o async for o in region(calls).compile().aobserve(fresh(), gate=gate)]
    kinds = [o.event.kind for o in observations]
    assert kinds[-1] == "stopped"
    assert "superstep.commit" not in kinds
    assert observations[-1].state.messages == []
    assert ("dispatch", "node", ("after",)) not in gate.calls


async def test_stop_before_a_batch_starts_no_branch():
    calls: list[str] = []
    gate = RecordingGate(stop={("dispatch", "fanout")})
    state = await region(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []
    assert state.status == "stopped"
    assert state.metadata["stop_reason"] == "dispatch.fanout.stopped"


def test_gate_names_are_exported_from_the_graph_package():
    import perpetua_core.graph as g

    for name in ("DispatchGate", "GateDecision", "GateRefused", "GateStopped",
                 "DispatchRequest", "CommitRequest"):
        assert name in g.__all__
