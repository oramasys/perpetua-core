"""Hypothesis property tests for MiniGraph engine invariants.

Three invariants:
1. Linear chains visit each node exactly once, in order.
2. Cycles always trip the max_steps cap regardless of cap value.
3. (implicit) Determinism: same state + same graph → same result (covered by
   the linear chain property when the strategy fixes the chain).
"""
import pytest
from hypothesis import given, strategies as st, settings
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, END, MaxStepsExceeded


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=1, max_value=10))
@pytest.mark.asyncio
async def test_linear_chain_visits_each_node_exactly_once(n: int):
    g = MiniGraph()
    names = [f"n{i}" for i in range(n)]
    for nm in names:
        g.add_node(nm, lambda s: {})
    g.set_entry(names[0])
    for i, nm in enumerate(names):
        g.add_edge(nm, names[i + 1] if i + 1 < n else END)
    result = await g.ainvoke(PerpetuaState(session_id="t1"))
    assert result.nodes_visited == names


@settings(max_examples=20, deadline=None)
@given(st.integers(min_value=2, max_value=15))
@pytest.mark.asyncio
async def test_cycle_always_raises_under_max_steps_cap(cap: int):
    g = MiniGraph(max_steps=cap)
    g.add_node("loop", lambda s: {})
    g.set_entry("loop")
    g.add_edge("loop", lambda s: "loop")
    with pytest.raises(MaxStepsExceeded):
        await g.ainvoke(PerpetuaState(session_id="t1"))
