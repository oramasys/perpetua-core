"""T1: the neutral DispatchGate seam inside the one scheduler.

Core owns only the protocol and the call sites. Whoever implements the gate
(Oramasys) owns authority, leases, stop state, delivery health and budget.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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
    """Test helper: fresh."""
    return PerpetuaState(session_id="t1")


class RecordingGate:
    """Allows everything unless told otherwise; records every boundary."""

    def __init__(self, *, refuse: set[tuple[str, str]] | None = None,
                 stop: set[tuple[str, str]] | None = None) -> None:
        """Initialise the instance and validate its arguments."""
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.refuse = refuse or set()
        self.stop = stop or set()

    def _decide(self, phase: str, boundary: str) -> GateDecision:
        """Test helper: decide."""
        if (phase, boundary) in self.refuse:
            return GateDecision.refuse(f"{phase}.{boundary}.refused")
        if (phase, boundary) in self.stop:
            return GateDecision.stop(f"{phase}.{boundary}.stopped")
        return GateDecision.allow()

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        """Test helper: before dispatch."""
        self.calls.append(("dispatch", request.boundary, request.nodes))
        return self._decide("dispatch", request.boundary)

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        """Test helper: before commit."""
        self.calls.append(("commit", request.boundary, request.nodes))
        return self._decide("commit", request.boundary)


class SettlingGate(RecordingGate):
    """Records the optional post-publication lifecycle callback."""

    def __init__(self) -> None:
        """Initialise the callback record alongside ordinary gate decisions."""
        super().__init__()
        self.settled: list[CommitRequest] = []

    async def after_commit(self, request: CommitRequest) -> None:
        """Record the exact identity Core associated with the published delta."""
        self.settled.append(request)


class BatchTrackingGate(RecordingGate):
    """Captures the opaque fan-out correlation token at both boundaries."""

    def __init__(self) -> None:
        """Initialise empty boundary records."""
        super().__init__()
        self.fanout: DispatchRequest | None = None
        self.region: CommitRequest | None = None

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        """Record the one fan-out request without changing admission."""
        if request.boundary == "fanout":
            self.fanout = request
        return await super().before_dispatch(request)

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        """Record the one region publication request without changing admission."""
        if request.boundary == "region":
            self.region = request
        return await super().before_commit(request)


def linear(calls: list[str]) -> MiniGraph:
    """Test helper: linear."""
    graph = MiniGraph()

    def a(_: PerpetuaState) -> dict:
        """Test helper: a."""
        calls.append("a")
        return {"scratchpad": {"a": 1}}

    graph.add_node("a", a)
    graph.set_entry("a")
    graph.add_edge("a", END)
    return graph


def region(calls: list[str], *, join: Join | None = None, reducer: bool = False) -> MiniGraph:
    """Test helper: region."""
    graph = MiniGraph()
    graph.add_node("plan", lambda s: {})

    def branch(name: str):
        """Test helper: branch."""
        async def run(_: PerpetuaState) -> dict:
            """Test helper: run."""
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
            """Test helper: fold."""
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
    """No gate keeps existing event sequence."""
    calls: list[str] = []
    plain = [o.event for o in [x async for x in linear(calls).compile().aobserve(fresh())]]
    gated = [o.event async for o in linear(calls).compile().aobserve(fresh(), gate=RecordingGate())]
    assert plain == gated


# --- every execution boundary is guarded ---------------------------------


async def test_linear_node_is_guarded_before_dispatch_and_before_commit():
    """Linear node is guarded before dispatch and before commit."""
    gate = RecordingGate()
    await linear([]).compile().ainvoke(fresh(), gate=gate)
    assert gate.calls == [("dispatch", "node", ("a",)), ("commit", "node", ("a",))]


async def test_post_commit_callback_receives_the_published_delta_digest() -> None:
    """Core exposes a neutral digest only after the synchronous publish boundary."""
    gate = SettlingGate()
    state = await linear([]).compile().ainvoke(fresh(), gate=gate)

    expected = hashlib.sha256(
        json.dumps({"scratchpad": {"a": 1}}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert state.scratchpad == {"a": 1}
    assert len(gate.settled) == 1
    assert gate.settled[0].result_digest == expected


async def test_region_threads_one_opaque_batch_id_from_dispatch_to_commit() -> None:
    """A fan-out's opaque batch token remains stable through region publication."""
    gate = BatchTrackingGate()

    await region([]).compile().ainvoke(fresh(), gate=gate)

    assert gate.fanout is not None
    assert gate.region is not None
    assert isinstance(gate.fanout.batch_id, str)
    assert gate.fanout.batch_id
    assert gate.region.batch_id == gate.fanout.batch_id


async def test_router_is_guarded_before_it_runs():
    """Router is guarded before it runs."""
    routed: list[str] = []
    graph = MiniGraph()
    graph.add_node("a", lambda s: {})

    def router(_: PerpetuaState) -> str:
        """Test helper: router."""
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
    """Region boundaries in order."""
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
    """Custom join is guarded before it runs."""
    joined: list[str] = []

    def admit(outcomes):
        """Test helper: admit."""
        joined.append("join")
        return [o.name for o in outcomes if o.ok]

    gate = RecordingGate(refuse={("dispatch", "join")})
    with pytest.raises(GateRefused):
        await region([], join=Join("custom", fn=admit)).compile().ainvoke(fresh(), gate=gate)
    assert joined == []


# --- refusal and stop never commit ----------------------------------------


async def test_refused_dispatch_never_calls_the_node():
    """Refused dispatch never calls the node."""
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "node")})
    with pytest.raises(GateRefused) as info:
        await linear(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []
    assert info.value.request.nodes == ("a",)


async def test_refused_commit_runs_the_node_but_publishes_nothing():
    """Refused commit runs the node but publishes nothing."""
    calls: list[str] = []
    gate = RecordingGate(refuse={("commit", "node")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in linear(calls).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert calls == ["a"]  # the call happened; its effects cannot be undone
    assert "node.end" not in seen


async def test_refused_batch_dispatches_no_branch():
    """Refused batch dispatches no branch."""
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "fanout")})
    with pytest.raises(GateRefused):
        await region(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []


async def test_refused_reducer_never_folds_and_never_commits():
    """Refused reducer never folds and never commits."""
    calls: list[str] = []
    gate = RecordingGate(refuse={("dispatch", "reducer")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in region(calls, reducer=True).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert sorted(calls) == ["b1", "b2"]  # settle-all preserved
    assert "superstep.commit" not in seen


async def test_refused_region_commit_publishes_nothing():
    """Refused region commit publishes nothing."""
    gate = RecordingGate(refuse={("commit", "region")})
    seen = []
    with pytest.raises(GateRefused):
        async for obs in region([]).compile().aobserve(fresh(), gate=gate):
            seen.append(obs.event.kind)
    assert "superstep.commit" not in seen


async def test_stop_ends_the_run_without_committing():
    """Stop ends the run without committing."""
    calls: list[str] = []
    gate = RecordingGate(stop={("commit", "node")})
    observations = [o async for o in linear(calls).compile().aobserve(fresh(), gate=gate)]
    last = observations[-1]
    assert last.event.kind == "stopped"
    assert last.event.terminal_reason == "stopped"
    assert last.state.scratchpad == {}
    assert "node.end" not in [o.event.kind for o in observations]


async def test_stop_returns_stopped_state_from_ainvoke():
    """Stop returns stopped state from ainvoke."""
    gate = RecordingGate(stop={("dispatch", "node")})
    state = await linear([]).compile().ainvoke(fresh(), gate=gate)
    assert state.status == "stopped"


# --- fail closed ----------------------------------------------------------


async def test_gate_error_propagates_and_nothing_commits():
    """Gate error propagates and nothing commits."""
    class Broken(RecordingGate):
        """Test helper Broken."""
        async def before_commit(self, request):
            """Test helper: before commit."""
            raise RuntimeError("ledger down")

    calls: list[str] = []
    with pytest.raises(RuntimeError, match="ledger down"):
        await linear(calls).compile().ainvoke(fresh(), gate=Broken())


async def test_non_decision_result_is_refused():
    """Non decision result is refused."""
    class Sloppy(RecordingGate):
        """Test helper Sloppy."""
        async def before_dispatch(self, request):
            """Test helper: before dispatch."""
            return True  # not a GateDecision

    with pytest.raises(TypeError):
        await linear([]).compile().ainvoke(fresh(), gate=Sloppy())


def test_decision_constructors_validate_reasons():
    """Decision constructors validate reasons."""
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
    """Minigraph ainvoke forwards the gate."""
    gate = RecordingGate()
    await linear([]).ainvoke(fresh(), gate=gate)
    assert gate.calls[0] == ("dispatch", "node", ("a",))


async def test_gate_stopped_exported_for_callers():
    """Gate stopped exported for callers."""
    assert issubclass(GateStopped, RuntimeError)


async def test_stop_inside_a_region_commits_nothing_and_ends_the_run():
    """Stop inside a region commits nothing and ends the run."""
    calls: list[str] = []
    gate = RecordingGate(stop={("commit", "region")})
    observations = [o async for o in region(calls).compile().aobserve(fresh(), gate=gate)]
    kinds = [o.event.kind for o in observations]
    assert kinds[-1] == "stopped"
    assert "superstep.commit" not in kinds
    assert observations[-1].state.messages == []
    assert ("dispatch", "node", ("after",)) not in gate.calls


async def test_stop_before_a_batch_starts_no_branch():
    """Stop before a batch starts no branch."""
    calls: list[str] = []
    gate = RecordingGate(stop={("dispatch", "fanout")})
    state = await region(calls).compile().ainvoke(fresh(), gate=gate)
    assert calls == []
    assert state.status == "stopped"
    assert state.metadata["stop_reason"] == "dispatch.fanout.stopped"


def test_gate_names_are_exported_from_the_graph_package():
    """Gate names are exported from the graph package."""
    import perpetua_core.graph as g

    for name in ("DispatchGate", "GateDecision", "GateRefused", "GateStopped",
                 "DispatchRequest", "CommitRequest"):
        assert name in g.__all__


# --- review fixes: check immediately before the call; counts after settling --


class LatchGate(RecordingGate):
    """Stops once ``latched`` is set, as a consumer would between observations."""

    latched = False

    def _decide(self, phase: str, boundary: str) -> GateDecision:
        """Test helper: decide."""
        if self.latched:
            return GateDecision.stop("consumer.stop")
        return super()._decide(phase, boundary)


async def test_stop_requested_while_paused_at_node_start_prevents_the_call():
    """Stop requested while paused at node start prevents the call."""
    calls: list[str] = []
    gate = LatchGate()
    kinds = []
    async for obs in linear(calls).compile().aobserve(fresh(), gate=gate):
        kinds.append(obs.event.kind)
        if obs.event.kind == "node.start":
            gate.latched = True  # consumer asks to stop while the iterator is paused
    assert calls == []
    assert kinds[-1] == "stopped"


async def test_stop_requested_while_paused_at_superstep_start_starts_no_branch():
    """Stop requested while paused at superstep start starts no branch."""
    calls: list[str] = []
    gate = LatchGate()
    async for obs in region(calls).compile().aobserve(fresh(), gate=gate):
        if obs.event.kind == "superstep.start":
            gate.latched = True
    assert calls == []


class CountingGate(RecordingGate):
    """Test helper CountingGate."""
    def __init__(self) -> None:
        """Initialise the instance and validate its arguments."""
        super().__init__()
        self.steps: dict[tuple[str, str], int] = {}

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        """Test helper: before dispatch."""
        self.steps[("dispatch", request.boundary)] = request.steps
        return await super().before_dispatch(request)

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        """Test helper: before commit."""
        self.steps[("commit", request.boundary)] = request.steps
        return await super().before_commit(request)


async def test_region_requests_count_completed_branches():
    """Region requests count completed branches."""
    def admit(outcomes):
        """Test helper: admit."""
        return [o.name for o in outcomes if o.ok]

    gate = CountingGate()
    await region([], join=Join("custom", fn=admit)).compile().ainvoke(fresh(), gate=gate)
    assert gate.steps[("dispatch", "fanout")] == 1  # only plan has completed
    assert gate.steps[("dispatch", "join")] == 3  # plan + two settled branches
    assert gate.steps[("dispatch", "reducer")] == 3
    assert gate.steps[("commit", "region")] == 3


async def test_non_finite_delta_fails_before_the_commit_gate() -> None:
    """NaN would digest as invalid JSON; it must be refused before before_commit."""
    graph = MiniGraph()
    graph.add_node("a", lambda s: {"scratchpad": {"x": float("nan")}})
    graph.set_entry("a")
    graph.add_edge("a", END)
    gate = RecordingGate()
    with pytest.raises(TypeError, match="canonical JSON"):
        await graph.compile().ainvoke(fresh(), gate=gate)
    assert ("commit", "node", ("a",)) not in gate.calls


async def test_gate_hashes_the_delta_it_publishes_even_if_the_node_mutates_it() -> None:
    """A node-shared mutable delta changing during the await cannot split digest from state."""
    shared: dict[str, Any] = {"scratchpad": {"v": 1}}
    seen: list[str] = []

    class MutatingGate(RecordingGate):
        """Mutates the node's shared delta while the commit decision is awaited."""

        async def before_commit(self, request: CommitRequest) -> GateDecision:
            """Record the digest, then mutate the shared object before publication."""
            seen.append(request.result_digest)
            shared["scratchpad"]["v"] = 2
            return await super().before_commit(request)

    graph = MiniGraph()
    graph.add_node("a", lambda s: shared)
    graph.set_entry("a")
    graph.add_edge("a", END)
    state = await graph.compile().ainvoke(fresh(), gate=MutatingGate())
    expected = hashlib.sha256(
        json.dumps({"scratchpad": {"v": 1}}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert seen == [expected]
    assert state.scratchpad == {"v": 1}


async def test_after_commit_failure_still_publishes_committed_state_then_stops() -> None:
    """A failed settlement hook must not hide the already-merged state from observers."""

    class FailingSettle(RecordingGate):
        """Raises from the optional settlement hook."""

        async def after_commit(self, request: CommitRequest) -> None:
            """Fail settlement after publication."""
            raise RuntimeError("settlement down")

    observed: list[tuple[str, dict[str, Any]]] = []
    graph = linear([]).compile()
    with pytest.raises(RuntimeError, match="settlement down"):
        async for obs in graph.aobserve(fresh(), gate=FailingSettle()):
            observed.append((obs.event.kind, dict(obs.state.scratchpad)))
    assert ("node.end", {"a": 1}) in observed
    assert all(kind != "done" for kind, _ in observed)


async def test_cancelled_settlement_still_publishes_committed_state_then_cancels() -> None:
    """Cancellation inside after_commit surfaces node.end before it propagates."""

    class CancelledSettle(RecordingGate):
        """Is cancelled inside the optional settlement hook."""

        async def after_commit(self, request: CommitRequest) -> None:
            """Raise cancellation after publication."""
            raise asyncio.CancelledError

    observed: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for obs in linear([]).compile().aobserve(fresh(), gate=CancelledSettle()):
            observed.append(obs.event.kind)
    assert "node.end" in observed
    assert "done" not in observed


async def test_cancelled_region_settlement_publishes_branches_and_commit() -> None:
    """A cancelled region settlement still surfaces branch node.end and superstep.commit."""

    class CancelledRegionSettle(RecordingGate):
        """Cancels settlement for the region commit only."""

        async def after_commit(self, request: CommitRequest) -> None:
            """Raise cancellation after the region publishes."""
            if request.boundary == "region":
                raise asyncio.CancelledError

    observed: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for obs in region([]).compile().aobserve(fresh(), gate=CancelledRegionSettle()):
            observed.append(obs.event.kind)
    assert "superstep.commit" in observed
    assert observed.count("node.end") >= 2
    assert "done" not in observed
