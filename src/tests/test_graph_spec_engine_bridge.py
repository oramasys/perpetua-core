from __future__ import annotations

import pytest

from perpetua_core.graph.engine import ConditionalEdge, END, START, MiniGraph
from perpetua_core.graph.lint import GraphSpecValidationError
from perpetua_core.state import PerpetuaState


def _state() -> PerpetuaState:
    return PerpetuaState(session_id="graphspec-test")


async def _node(state: PerpetuaState) -> dict:
    return {}


def test_minigraph_describe_returns_structural_spec() -> None:
    graph = MiniGraph(max_steps=7)
    graph.add_node("a", _node)
    graph.add_node("b", _node)
    graph.set_entry("a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)

    spec = graph.describe()

    assert spec.max_steps == 7
    assert tuple(node.name for node in spec.nodes) == ("a", "b")
    assert any(
        edge.source == START and edge.kind == "static" and edge.target == "a"
        for edge in spec.edges
    )


def test_describe_does_not_execute_nodes_or_router() -> None:
    calls: list[str] = []

    async def node(state: PerpetuaState) -> dict:
        calls.append("node")
        return {}

    def router(state: PerpetuaState) -> str:
        calls.append("router")
        return END

    graph = MiniGraph()
    graph.add_node("a", node)
    graph.set_entry("a")
    graph.add_edge("a", router)

    graph.describe()

    assert calls == []


def test_compiled_describe_is_detached_from_builder_mutation() -> None:
    graph = MiniGraph()
    graph.add_node("a", _node)
    graph.set_entry("a")
    graph.add_edge("a", END)
    compiled = graph.compile()
    before = compiled.describe()

    graph.add_node("b", _node)
    graph.add_edge("a", "b")

    assert compiled.describe() == before
    assert tuple(node.name for node in compiled.describe().nodes) == ("a",)


def test_conditional_edge_declares_targets_without_running_router() -> None:
    calls: list[str] = []

    def router(state: PerpetuaState) -> str:
        calls.append("router")
        return "fast"

    graph = MiniGraph()
    graph.add_node("route", _node)
    graph.add_node("fast", _node)
    graph.add_node("slow", _node)
    graph.set_entry("route")
    graph.add_edge(
        "route",
        ConditionalEdge(router=router, declared_targets=("slow", "fast")),
    )
    graph.add_edge("fast", END)
    graph.add_edge("slow", END)

    spec = graph.describe()
    edge = next(edge for edge in spec.edges if edge.source == "route")

    assert edge.kind == "conditional"
    assert edge.declared_targets == ("fast", "slow")
    assert calls == []


@pytest.mark.asyncio
async def test_conditional_edge_runtime_uses_same_canonical_scheduler() -> None:
    def router(state: PerpetuaState) -> str:
        return "fast"

    graph = MiniGraph()
    graph.add_node("route", _node)
    graph.add_node("fast", _node)
    graph.set_entry("route")
    graph.add_edge("route", ConditionalEdge(router, ("fast",)))
    graph.add_edge("fast", END)

    result = await graph.ainvoke(_state())

    assert result.status == "done"
    assert result.nodes_visited == ["route", "fast"]


def test_compile_validated_rejects_unknown_entry_target() -> None:
    graph = MiniGraph()
    graph.add_node("a", _node)
    graph.set_entry("missing")

    with pytest.raises(GraphSpecValidationError):
        graph.compile_validated()


def test_plain_compile_keeps_legacy_deferred_validation_behavior() -> None:
    graph = MiniGraph()
    graph.add_node("a", _node)
    graph.set_entry("missing")

    compiled = graph.compile()

    assert compiled is not None
    assert not compiled.validate().valid
