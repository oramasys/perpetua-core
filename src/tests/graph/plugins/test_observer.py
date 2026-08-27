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
async def test_two_spy_observers_receive_identical_complete_delivery() -> None:
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "value": 42}},
    ).set_entry("work")

    class Spy:
        def __init__(self) -> None:
            self.records: list[tuple[str, str | None, int, dict | None]] = []

        async def on_observation(self, observation: GraphObservation) -> None:
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
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "value": 42}},
    ).set_entry("work")

    class Checkpointer:
        def __init__(self) -> None:
            self.delivered: list[str] = []
            self.persisted: list[dict] = []

        def on_observation(self, observation: GraphObservation) -> None:
            self.delivered.append(observation.event.kind)
            if observation.event.kind == "node.end":
                assert observation.delta is not None
                self.persisted.append(observation.delta)

    class Tracer:
        def __init__(self) -> None:
            self.delivered: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            self.delivered.append(observation.event.kind)

    checkpointer = Checkpointer()
    tracer = Tracer()

    await run_with_plugins(graph, state(), [checkpointer, tracer])

    assert checkpointer.delivered == tracer.delivered
    assert checkpointer.persisted == [{"scratchpad": {"value": 42}}]


@pytest.mark.asyncio
async def test_plugin_run_is_state_equivalent_to_no_plugin_baseline() -> None:
    graph = MiniGraph().add_node(
        "work",
        lambda s: {
            "scratchpad": {**s.scratchpad, "value": 42},
            "metadata": {**s.metadata, "observed": True},
        },
    ).set_entry("work")

    class Noop:
        def on_observation(self, observation: GraphObservation) -> None:
            pass

    baseline = await graph.compile().ainvoke(state())
    observed = await run_with_plugins(graph.compile(), state(), [Noop(), Noop()])

    assert observed.model_dump() == baseline.model_dump()


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
