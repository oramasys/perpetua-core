import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, END


@pytest.mark.asyncio
async def test_set_entry_is_equivalent_to_add_edge_from_start():
    g = MiniGraph()
    g.add_node("a", lambda s: {"scratchpad": {"x": 1}})
    g.set_entry("a")
    g.add_edge("a", END)
    state = await g.ainvoke(PerpetuaState(session_id="t1"))
    assert state.scratchpad["x"] == 1


@pytest.mark.asyncio
async def test_compile_returns_frozen_graph_with_ainvoke():
    g = MiniGraph()
    g.add_node("a", lambda s: {"scratchpad": {"x": 1}})
    g.set_entry("a")
    g.add_edge("a", END)
    compiled = g.compile()
    state = await compiled.ainvoke(PerpetuaState(session_id="t1"))
    assert state.scratchpad["x"] == 1
    # compile() must return a distinct object — not the same MiniGraph (freezes mutation)
    assert compiled is not g
