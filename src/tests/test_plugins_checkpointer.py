"""TDD: checkpointer plugin — save/load round-trip and observer fan-out."""
from __future__ import annotations

import pytest

from perpetua_core.graph.engine import GraphObservation, MiniGraph
from perpetua_core.graph.plugins.checkpointer import SqliteCheckpointer
from perpetua_core.graph.plugins.observer import run_with_plugins
from perpetua_core.state import PerpetuaState


@pytest.fixture
async def ckpt(tmp_path):
    c = SqliteCheckpointer(str(tmp_path / "test.db"))
    await c.init_db()
    return c


async def test_save_and_load_roundtrip(ckpt):
    s = PerpetuaState(session_id="sess1", status="running", retry_count=2)
    await ckpt.save(s, node="node_x")
    loaded = await ckpt.load_latest("sess1")
    assert loaded is not None
    assert loaded.session_id == "sess1"
    assert loaded.status == "running"
    assert loaded.retry_count == 2


async def test_load_returns_none_for_unknown_session(ckpt):
    result = await ckpt.load_latest("nonexistent")
    assert result is None


async def test_load_returns_most_recent(ckpt):
    s1 = PerpetuaState(session_id="s", retry_count=1)
    s2 = PerpetuaState(session_id="s", retry_count=2)
    await ckpt.save(s1, node="a")
    await ckpt.save(s2, node="b")
    loaded = await ckpt.load_latest("s")
    assert loaded.retry_count == 2


async def test_checkpointer_and_tracer_observe_same_run(ckpt):
    graph = MiniGraph().add_node(
        "work",
        lambda s: {"scratchpad": {**s.scratchpad, "answer": 42}},
    ).set_entry("work")

    class Tracer:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def on_observation(self, observation: GraphObservation) -> None:
            self.kinds.append(observation.event.kind)

    tracer = Tracer()
    result = await run_with_plugins(
        graph,
        PerpetuaState(session_id="shared-run"),
        [ckpt, tracer],
    )

    loaded = await ckpt.load_latest("shared-run")
    assert loaded is not None
    assert loaded.scratchpad["answer"] == 42
    assert result.status == "done"
    assert tracer.kinds == [
        "edge.selected",
        "node.start",
        "node.end",
        "edge.selected",
        "done",
    ]
