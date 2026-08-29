from __future__ import annotations

from copy import deepcopy

import pytest

from perpetua_core.graph.engine import GraphObservation, MiniGraph
from perpetua_core.graph.plugins.observer import run_with_plugins
from perpetua_core.state import PerpetuaState


@pytest.mark.asyncio
async def test_unknown_route_fails_at_resolution_boundary() -> None:
    async def step(_state: PerpetuaState) -> dict[str, object]:
        return {}

    graph = MiniGraph().add_node("step", step).set_entry("step")
    graph.add_edge("step", lambda _state: "missing")

    with pytest.raises(ValueError, match="unknown node 'missing'"):
        await graph.ainvoke(PerpetuaState(session_id="route-boundary"))


@pytest.mark.asyncio
async def test_plugin_payloads_are_isolated_from_each_other_and_live_state() -> None:
    async def step(_state: PerpetuaState) -> dict[str, object]:
        return {"scratchpad": {"result": 42}}

    graph = MiniGraph().add_node("step", step).set_entry("step")

    class MutatingPlugin:
        def on_observation(self, observation: GraphObservation) -> None:
            observation.state.scratchpad["poison"] = True
            if observation.delta is not None:
                observation.delta["poison"] = True

    class RecordingPlugin:
        def __init__(self) -> None:
            self.states: list[dict[str, object]] = []
            self.deltas: list[dict[str, object] | None] = []

        def on_observation(self, observation: GraphObservation) -> None:
            self.states.append(deepcopy(observation.state.scratchpad))
            self.deltas.append(deepcopy(observation.delta))

    recorder = RecordingPlugin()
    final_state = await run_with_plugins(
        graph,
        PerpetuaState(session_id="observer-isolation"),
        [MutatingPlugin(), recorder],
    )

    assert final_state.scratchpad == {"result": 42}
    assert all("poison" not in state for state in recorder.states)
    assert all(delta is None or "poison" not in delta for delta in recorder.deltas)
