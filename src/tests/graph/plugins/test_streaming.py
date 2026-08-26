"""Streaming must adapt the canonical MiniGraph scheduler, not copy it."""
from __future__ import annotations

import inspect

import pytest

from perpetua_core.graph.engine import MiniGraph
from perpetua_core.graph.plugins import streaming
from perpetua_core.graph.plugins.streaming import astream
from perpetua_core.state import PerpetuaState


@pytest.mark.asyncio
async def test_streaming_matches_compiled_structural_events():
    graph = MiniGraph().add_node("work", lambda s: {}).set_entry("work")
    initial = PerpetuaState(session_id="stream")

    expected = [event async for event in graph.compile().asteps(initial)]
    actual = [event async for event in astream(graph, initial)]

    assert actual == expected


def test_streaming_does_not_traverse_private_graph_topology():
    source = inspect.getsource(streaming)
    assert "._nodes" not in source
    assert "._edges" not in source
