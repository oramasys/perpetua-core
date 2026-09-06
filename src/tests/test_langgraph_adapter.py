"""Tests for LangGraphExporter against the real MiniGraph surface and an
installed langgraph, using the public CompiledGraph.nodes/.edges accessors
rather than reaching into private state."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from perpetua_core.graph.adapters.langgraph_adapter import LangGraphExporter
from perpetua_core.graph.engine import END, START, MiniGraph
from perpetua_core.state import PerpetuaState


def make_state(**kwargs: object) -> PerpetuaState:
    return PerpetuaState(session_id="test", **kwargs)


def test_static_topology_exports_and_runs() -> None:
    def node_a(state: PerpetuaState) -> dict:
        return {"scratchpad": {**state.scratchpad, "a": True}}

    def node_b(state: PerpetuaState) -> dict:
        return {"scratchpad": {**state.scratchpad, "b": True}}

    g = MiniGraph()
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)

    compiled_lg = LangGraphExporter.to_langgraph(g, PerpetuaState)
    result = asyncio.run(compiled_lg.ainvoke(make_state()))

    assert result["scratchpad"] == {"a": True, "b": True}


def test_conditional_edge_exports_and_routes() -> None:
    def router_node(state: PerpetuaState) -> dict:
        return {}

    def node_a(state: PerpetuaState) -> dict:
        return {"scratchpad": {"route": "a"}}

    def node_b(state: PerpetuaState) -> dict:
        return {"scratchpad": {"route": "b"}}

    def router(state: PerpetuaState) -> str:
        return "a" if state.metadata.get("take") == "a" else "b"

    g = MiniGraph()
    g.add_node("router", router_node)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "router")
    g.add_edge("router", router)
    g.add_edge("a", END)
    g.add_edge("b", END)

    compiled_lg = LangGraphExporter.to_langgraph(g, PerpetuaState)

    result_a = asyncio.run(compiled_lg.ainvoke(make_state(metadata={"take": "a"})))
    result_b = asyncio.run(compiled_lg.ainvoke(make_state(metadata={"take": "b"})))

    assert result_a["scratchpad"] == {"route": "a"}
    assert result_b["scratchpad"] == {"route": "b"}


def test_missing_langgraph_raises_clear_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "langgraph.graph":
            raise ImportError("simulated: langgraph not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    g = MiniGraph()
    g.add_node("a", lambda state: {})
    g.add_edge(START, "a")
    g.add_edge("a", END)

    with pytest.raises(ImportError, match="pip install langgraph"):
        LangGraphExporter.to_langgraph(g, PerpetuaState)
