import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, START, END, MaxStepsExceeded


@pytest.mark.asyncio
async def test_max_steps_guard_breaks_infinite_loop():
    g = MiniGraph(max_steps=5)
    g.add_node("a", lambda s: {"scratchpad": {"hits": s.scratchpad.get("hits", 0) + 1}})
    g.add_edge(START, "a")
    g.add_edge("a", lambda s: "a")  # loops forever absent the guard
    with pytest.raises(MaxStepsExceeded) as exc:
        await g.ainvoke(PerpetuaState(session_id="t1"))
    assert exc.value.steps == 5


@pytest.mark.asyncio
async def test_default_max_steps_is_generous_enough_for_typical_graphs():
    # A bounded 3-node graph must complete with the default cap.
    g = MiniGraph()
    g.add_node("a", lambda s: {"scratchpad": {"step": "a"}})
    g.add_node("b", lambda s: {"scratchpad": {"step": "b"}})
    g.add_node("c", lambda s: {"scratchpad": {"step": "c"}})
    g.add_edge(START, "a"); g.add_edge("a", "b"); g.add_edge("b", "c"); g.add_edge("c", END)
    result = await g.ainvoke(PerpetuaState(session_id="t1"))
    assert result.status == "done"
