"""Tests for LangGraphExporter against the real MiniGraph surface and an
installed langgraph, using the public CompiledGraph.nodes/.edges accessors
rather than reaching into private state."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from perpetua_core.graph.adapters.langgraph_adapter import LangGraphExporter
from perpetua_core.graph.engine import END, START, ConditionalEdge, MiniGraph
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
            raise ModuleNotFoundError("simulated: langgraph not installed", name="langgraph")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    g = MiniGraph()
    g.add_node("a", lambda state: {})
    g.add_edge(START, "a")
    g.add_edge("a", END)

    with pytest.raises(ImportError, match="pip install langgraph"):
        LangGraphExporter.to_langgraph(g, PerpetuaState)


def test_broken_langgraph_dependency_is_not_reported_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed optional frameworks must surface broken transitive imports verbatim."""
    import builtins
    real_import = builtins.__import__
    failure = ModuleNotFoundError("broken dependency", name="broken_transitive")
    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        """Plant a transitive failure at the lazy bridge boundary."""
        if name == "langgraph.graph":
            raise failure
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModuleNotFoundError) as error:
        LangGraphExporter.to_langgraph(MiniGraph(), PerpetuaState)
    assert error.value is failure


@pytest.mark.parametrize("target", ["b", "c", END])
def test_advisory_targets_do_not_restrict_valid_routes(target: str) -> None:
    """Compare simple native/exported routes, including undeclared nodes and END."""
    graph = MiniGraph().add_node("a", lambda state: {})
    for name in ("b", "c"):
        graph.add_node(name, lambda state, selected=name: {"scratchpad": {"route": selected}})
        graph.add_edge(name, END)
    graph.add_edge(START, "a")
    graph.add_edge("a", ConditionalEdge(lambda state: target, ("b",)))
    native = asyncio.run(graph.compile().ainvoke(make_state()))
    exported = asyncio.run(LangGraphExporter.to_langgraph(graph, PerpetuaState).ainvoke(make_state()))
    assert native.scratchpad == exported["scratchpad"]
    assert native.scratchpad == ({} if target == END else {"route": target})


def test_fanout_region_is_refused_not_misexported() -> None:
    from perpetua_core.graph.engine import FanOut

    g = MiniGraph()
    for name in ("plan", "a", "b", "after"):
        g.add_node(name, lambda s: {})
    g.set_entry("plan")
    g.add_edge("plan", FanOut(("a", "b"), "after"))
    g.add_edge("after", END)

    with pytest.raises(NotImplementedError, match="fan-out region"):
        LangGraphExporter.to_langgraph(g, PerpetuaState)
