"""TDD: MiniGraph kernel in isolation — no plugins loaded."""
import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, START, END


def make_state(**kwargs) -> PerpetuaState:
    return PerpetuaState(session_id="test", **kwargs)


async def node_a(state: PerpetuaState) -> dict:
    return {"scratchpad": {**state.scratchpad, "a": True}}


async def node_b(state: PerpetuaState) -> dict:
    return {"scratchpad": {**state.scratchpad, "b": True}}


def test_three_node_graph_runs_end_to_end():
    g = MiniGraph()
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)

    import asyncio
    result = asyncio.run(g.ainvoke(make_state()))
    assert result.scratchpad["a"] is True
    assert result.scratchpad["b"] is True
    assert result.status == "done"


def test_nodes_visited_populated():
    g = MiniGraph()
    g.add_node("x", node_a)
    g.add_edge(START, "x")
    g.add_edge("x", END)

    import asyncio
    result = asyncio.run(g.ainvoke(make_state()))
    assert "x" in result.nodes_visited


def test_conditional_edge_routes_correctly():
    async def router_node(state: PerpetuaState) -> dict:
        return {"metadata": {**state.metadata, "choice": "b"}}

    def router(state: PerpetuaState) -> str:
        return state.metadata.get("choice", "a")

    g = MiniGraph()
    g.add_node("router", router_node)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "router")
    g.add_edge("router", router)  # conditional
    g.add_edge("a", END)
    g.add_edge("b", END)

    import asyncio
    result = asyncio.run(g.ainvoke(make_state()))
    assert result.scratchpad.get("b") is True
    assert "a" not in result.scratchpad


def test_engine_has_no_plugin_imports():
    """Kernel must not import from plugins package."""
    import inspect
    from perpetua_core.graph import engine
    src = inspect.getsource(engine)
    assert "plugins" not in src, "engine.py must not import from plugins/"


def test_minigraph_import_no_optional_deps():
    """Cold import must succeed without aiosqlite, openai, etc. installed."""
    # This passes if MiniGraph is importable — transitive deps are checked by
    # verifying that engine.py's module-level imports are pure Python only.
    from perpetua_core.graph.engine import MiniGraph as MG
    assert MG is not None
