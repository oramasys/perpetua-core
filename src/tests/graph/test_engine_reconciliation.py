"""Final MiniGraph reconciliation regression tests."""
from __future__ import annotations

import asyncio
import sys

import pytest

from perpetua_core.graph.engine import (
    END,
    CompiledGraph,
    MaxStepsExceeded,
    MiniGraph,
)
from perpetua_core.graph.plugins.tool_node import ToolNode
from perpetua_core.state import PerpetuaState


def state() -> PerpetuaState:
    return PerpetuaState(session_id="reconcile")


@pytest.mark.asyncio
async def test_async_function_node_is_awaited():
    async def node(s: PerpetuaState) -> dict:
        return {"scratchpad": {**s.scratchpad, "kind": "async-fn"}}

    graph = MiniGraph().add_node("work", node).set_entry("work")
    result = await graph.ainvoke(state())
    assert result.scratchpad["kind"] == "async-fn"


@pytest.mark.asyncio
async def test_sync_function_node_runs():
    def node(s: PerpetuaState) -> dict:
        return {"scratchpad": {**s.scratchpad, "kind": "sync-fn"}}

    graph = MiniGraph().add_node("work", node).set_entry("work")
    result = await graph.ainvoke(state())
    assert result.scratchpad["kind"] == "sync-fn"


@pytest.mark.asyncio
async def test_async_callable_object_is_awaited():
    class AsyncCallable:
        async def __call__(self, s: PerpetuaState) -> dict:
            return {"scratchpad": {**s.scratchpad, "kind": "async-object"}}

    graph = MiniGraph().add_node("work", AsyncCallable()).set_entry("work")
    result = await graph.ainvoke(state())
    assert result.scratchpad["kind"] == "async-object"


@pytest.mark.asyncio
async def test_sync_callable_returning_awaitable_is_awaited():
    async def delta() -> dict:
        return {"scratchpad": {"kind": "returned-awaitable"}}

    def node(_: PerpetuaState):
        return delta()

    graph = MiniGraph().add_node("work", node).set_entry("work")
    result = await graph.ainvoke(state())
    assert result.scratchpad["kind"] == "returned-awaitable"


@pytest.mark.asyncio
async def test_real_tool_node_runs_inside_minigraph():
    node = ToolNode(
        argv=[sys.executable, "-c", "print('tool-via-graph')"],
        output_key="tool_out",
    )
    graph = MiniGraph().add_node("tool", node).set_entry("tool")
    result = await graph.ainvoke(state())
    assert result.scratchpad["tool_out"] == "tool-via-graph"


@pytest.mark.asyncio
async def test_visit_provenance_stays_ordered_list():
    graph = MiniGraph()
    graph.add_node("a", lambda s: {})
    graph.add_node("b", lambda s: {})
    graph.set_entry("a").add_edge("a", "b").add_edge("b", END)
    result = await graph.ainvoke(state())
    assert result.nodes_visited == ["a", "b"]
    assert isinstance(result.nodes_visited, list)


@pytest.mark.asyncio
async def test_conditional_edge_observes_updated_state():
    graph = MiniGraph()
    graph.add_node("route", lambda s: {"metadata": {**s.metadata, "to": "b"}})
    graph.add_node("a", lambda s: {"scratchpad": {"chosen": "a"}})
    graph.add_node("b", lambda s: {"scratchpad": {"chosen": "b"}})
    graph.set_entry("route")
    graph.add_edge("route", lambda s: s.metadata["to"])
    result = await graph.ainvoke(state())
    assert result.scratchpad["chosen"] == "b"


@pytest.mark.asyncio
async def test_compiled_topology_is_detached_from_builder_mutation():
    graph = MiniGraph()
    graph.add_node("work", lambda s: {"scratchpad": {"value": "original"}})
    graph.set_entry("work")
    compiled = graph.compile()

    graph.add_node("work", lambda s: {"scratchpad": {"value": "mutated"}})
    result = await compiled.ainvoke(state())
    assert result.scratchpad["value"] == "original"


@pytest.mark.asyncio
async def test_none_node_delta_fails_contract():
    graph = MiniGraph().add_node("broken", lambda s: None).set_entry("broken")
    with pytest.raises(TypeError, match="expected dict delta"):
        await graph.ainvoke(state())


@pytest.mark.asyncio
async def test_empty_route_is_not_silent_success():
    graph = MiniGraph().add_node("route", lambda s: {}).set_entry("route")
    graph.add_edge("route", lambda s: "")
    with pytest.raises(ValueError, match="empty node name"):
        await graph.ainvoke(state())


@pytest.mark.asyncio
async def test_non_string_route_fails_contract():
    graph = MiniGraph().add_node("route", lambda s: {}).set_entry("route")
    graph.add_edge("route", lambda s: None)  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError, match="expected node name or END string"):
        await graph.ainvoke(state())


@pytest.mark.asyncio
async def test_structural_interrupt_payload_is_optional():
    class Interrupt(Exception):
        def __init__(self, prompt: str) -> None:
            self.prompt = prompt
            super().__init__(prompt)

    def pause(_: PerpetuaState) -> dict:
        raise Interrupt("human input required")

    graph = MiniGraph().add_node("pause", pause).set_entry("pause")
    result = await graph.ainvoke(state())
    assert result.status == "interrupted"
    assert result.metadata["interrupt_node"] == "pause"
    assert result.metadata["interrupt_prompt"] == "human input required"
    assert result.metadata["interrupt_payload"] is None


@pytest.mark.asyncio
async def test_max_steps_reports_completed_steps_and_last_entered_node():
    graph = MiniGraph(max_steps=2)
    graph.add_node("loop", lambda s: {})
    graph.set_entry("loop").add_edge("loop", "loop")
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await graph.ainvoke(state())
    assert exc_info.value.steps == 2
    assert exc_info.value.last_node == "loop"


@pytest.mark.asyncio
async def test_asteps_exposes_structural_event_sequence():
    graph = MiniGraph().add_node("work", lambda s: {}).set_entry("work")
    compiled: CompiledGraph = graph.compile()
    events = [event async for event in compiled.asteps(state())]
    assert [event.kind for event in events] == [
        "edge.selected",
        "node.start",
        "node.end",
        "edge.selected",
        "done",
    ]
    assert events[0].node == "__start__"
    assert events[0].target == "work"
    assert events[-1].steps == 1
    assert events[-1].terminal_reason == "done"
