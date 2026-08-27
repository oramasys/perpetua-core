"""Observer fan-out regression tests."""
from __future__ import annotations

import asyncio

import pytest

from perpetua_core.graph.engine import GraphObservation, MiniGraph
from perpetua_core.graph.plugins.observer import run_with_plugins
from perpetua_core.state import PerpetuaState


def state() -> PerpetuaState:
    return PerpetuaState(session_id="observer")


@pytest.mark.asyncio
async def test_two_observers_receive_the_same_complete_run() -> None:
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "value": 42}},
    ).set_entry("work")

    class Checkpointer:
        def __init__(self) -> None:
            self.node_end_states: list[PerpetuaState] = []
            self.deltas: list[dict] = []

        async def on_observation(self, observation: GraphObservation) -> None:
            await asyncio.sleep(0)
            if observation.event.kind == "node.end":
                self.node_end_states.append(observation.state)
                assert observation.delta is not None
                self.deltas.append(observation.delta)

    class Tracer:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            self.kinds.append(observation.event.kind)

    checkpointer = Checkpointer()
    tracer = Tracer()

    result = await run_with_plugins(
        graph,
        state(),
        [checkpointer, tracer],
    )

    assert result.status == "done"
    assert result.scratchpad["value"] == 42
    assert tracer.kinds == [
        "edge.selected",
        "node.start",
        "node.end",
        "edge.selected",
        "done",
    ]
    assert len(checkpointer.node_end_states) == 1
    assert checkpointer.node_end_states[0].scratchpad["value"] == 42
    assert checkpointer.deltas == [{"scratchpad": {"value": 42}}]


@pytest.mark.asyncio
async def test_plugin_delivery_is_registration_order_and_fail_closed() -> None:
    graph = MiniGraph().add_node("work", lambda s: {}).set_entry("work")
    calls: list[str] = []

    class First:
        def on_observation(self, observation: GraphObservation) -> None:
            calls.append(f"first:{observation.event.kind}")

    class Broken:
        def on_observation(self, observation: GraphObservation) -> None:
            calls.append(f"broken:{observation.event.kind}")
            raise RuntimeError("observer failed")

    class NeverReached:
        def on_observation(self, observation: GraphObservation) -> None:
            calls.append(f"last:{observation.event.kind}")

    with pytest.raises(RuntimeError, match="observer failed"):
        await run_with_plugins(
            graph,
            state(),
            [First(), Broken(), NeverReached()],
        )

    assert calls == ["first:edge.selected", "broken:edge.selected"]
