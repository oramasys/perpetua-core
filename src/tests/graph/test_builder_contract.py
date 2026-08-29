"""Regression tests for MiniGraph's construction/runtime boundary."""
from perpetua_core.graph.engine import MiniGraph
from perpetua_core.state import PerpetuaState


def _state() -> PerpetuaState:
    return PerpetuaState(session_id="builder")


def test_add_node_and_add_edge_mutate_builder_and_return_self() -> None:
    graph = MiniGraph()

    assert graph.add_node("work", lambda state: {}) is graph
    assert graph.set_entry("work") is graph
    assert "work" in graph._nodes


def test_compile_detaches_runtime_from_later_builder_mutation() -> None:
    graph = MiniGraph()
    graph.add_node("work", lambda state: {"scratchpad": {"value": "original"}})
    graph.set_entry("work")
    compiled = graph.compile()

    graph.add_node("work", lambda state: {"scratchpad": {"value": "mutated"}})

    import asyncio

    result = asyncio.run(compiled.ainvoke(_state()))
    assert result.scratchpad["value"] == "original"
