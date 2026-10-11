"""Observer fan-out regression tests."""
from __future__ import annotations

import asyncio

import pytest

from perpetua_core.graph.engine import GraphObservation, MiniGraph
from perpetua_core.graph.plugins.observer import run_with_plugins
from perpetua_core.state import PerpetuaState


def state() -> PerpetuaState:
    """Test helper: state."""
    return PerpetuaState(session_id="observer")


@pytest.mark.asyncio
async def test_two_spy_observers_receive_identical_complete_delivery() -> None:
    """Two spy observers receive identical complete delivery."""
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "value": 42}},
    ).set_entry("work")

    class Spy:
        """Test helper Spy."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.records: list[tuple[str, str | None, int, dict | None]] = []

        async def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            await asyncio.sleep(0)
            self.records.append(
                (
                    observation.event.kind,
                    observation.event.node,
                    observation.event.steps,
                    observation.delta,
                )
            )

    first = Spy()
    second = Spy()

    result = await run_with_plugins(graph, state(), [first, second])

    assert result.status == "done"
    assert first.records == second.records
    assert [kind for kind, *_ in first.records] == [
        "edge.selected",
        "node.start",
        "node.end",
        "edge.selected",
        "done",
    ]
    assert first.records[2][3] == {"scratchpad": {"value": 42}}


@pytest.mark.asyncio
async def test_semantic_plugins_may_filter_after_identical_delivery() -> None:
    """Semantic plugins may filter after identical delivery."""
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "value": 42}},
    ).set_entry("work")

    class Checkpointer:
        """Test helper Checkpointer."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.delivered: list[str] = []
            self.persisted: list[dict] = []

        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            self.delivered.append(observation.event.kind)
            if observation.event.kind == "node.end":
                assert observation.delta is not None
                self.persisted.append(observation.delta)

    class Tracer:
        """Test helper Tracer."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.delivered: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            self.delivered.append(observation.event.kind)

    checkpointer = Checkpointer()
    tracer = Tracer()

    await run_with_plugins(graph, state(), [checkpointer, tracer])

    assert checkpointer.delivered == tracer.delivered
    assert checkpointer.persisted == [{"scratchpad": {"value": 42}}]


@pytest.mark.asyncio
async def test_plugin_run_is_state_equivalent_to_no_plugin_baseline() -> None:
    """Plugin run is state equivalent to no plugin baseline."""
    graph = MiniGraph().add_node(
        "work",
        lambda s: {
            "scratchpad": {**s.scratchpad, "value": 42},
            "metadata": {**s.metadata, "observed": True},
        },
    ).set_entry("work")

    class Noop:
        """Test helper Noop."""
        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            pass

    baseline = await graph.compile().ainvoke(state())
    observed = await run_with_plugins(graph.compile(), state(), [Noop(), Noop()])

    assert observed.model_dump() == baseline.model_dump()


@pytest.mark.asyncio
async def test_plugin_delivery_is_registration_order_and_fail_closed() -> None:
    """Plugin delivery is registration order and fail closed."""
    graph = MiniGraph().add_node("work", lambda s: {}).set_entry("work")
    calls: list[str] = []

    class First:
        """Test helper First."""
        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            calls.append(f"first:{observation.event.kind}")

    class Broken:
        """Test helper Broken."""
        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            calls.append(f"broken:{observation.event.kind}")
            raise RuntimeError("observer failed")

    class NeverReached:
        """Test helper NeverReached."""
        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            calls.append(f"last:{observation.event.kind}")

    with pytest.raises(RuntimeError, match="observer failed"):
        await run_with_plugins(
            graph,
            state(),
            [First(), Broken(), NeverReached()],
        )

    assert calls == ["first:edge.selected", "broken:edge.selected"]


@pytest.mark.asyncio
async def test_run_with_plugins_supports_optional_delivery_and_gate() -> None:
    """Run with plugins supports optional delivery and gate."""
    from perpetua_core.graph.gate import DispatchGate, GateDecision, DispatchRequest, CommitRequest

    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "count": 1}},
    ).set_entry("work")

    class DeliverySpy:
        """Test helper DeliverySpy."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.kinds: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            self.kinds.append(observation.event.kind)

    class GateSpy(DispatchGate):
        """Test helper GateSpy."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.dispatched: list[str] = []

        async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
            """Test helper: before dispatch."""
            self.dispatched.append(request.boundary)
            return GateDecision.allow()

        async def before_commit(self, request: CommitRequest) -> GateDecision:
            """Test helper: before commit."""
            return GateDecision.allow()

    delivery = DeliverySpy()
    gate = GateSpy()

    res = await run_with_plugins(
        graph,
        state(),
        delivery=delivery,
        gate=gate,
    )
    assert res.status == "done"
    assert "node.start" in delivery.kinds
    assert "node" in gate.dispatched


@pytest.mark.asyncio
async def test_run_with_plugins_gate_stops_execution() -> None:
    """Run with plugins gate stops execution."""
    from perpetua_core.graph.gate import DispatchGate, GateDecision, DispatchRequest, CommitRequest

    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "count": 1}},
    ).set_entry("work")

    class StopGate(DispatchGate):
        """Test helper StopGate."""
        async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
            """Test helper: before dispatch."""
            if request.boundary == "node":
                return GateDecision.stop("operator_cancel")
            return GateDecision.allow()

        async def before_commit(self, request: CommitRequest) -> GateDecision:
            """Test helper: before commit."""
            return GateDecision.allow()

    class DeliverySpy:
        """Test helper DeliverySpy."""
        def __init__(self) -> None:
            """Initialise the instance and validate its arguments."""
            self.kinds: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            """Test helper: on observation."""
            self.kinds.append(observation.event.kind)

    delivery = DeliverySpy()
    res = await run_with_plugins(
        graph,
        state(),
        delivery=delivery,
        gate=StopGate(),
    )
    assert res.status == "stopped"
    assert "stopped" in delivery.kinds

