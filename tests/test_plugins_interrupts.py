"""TDD: HITL interrupt plugin."""
import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, START, END
from perpetua_core.graph.plugins.interrupts import Interrupt


async def gating_node(state: PerpetuaState) -> dict:
    raise Interrupt("Please confirm this action.", payload={"action": "delete"})


async def post_gate_node(state: PerpetuaState) -> dict:
    return {"scratchpad": {**state.scratchpad, "confirmed": True}}


def test_interrupt_sets_status_interrupted():
    import asyncio
    g = MiniGraph(interrupt_handler="store")
    g.add_node("gate", gating_node)
    g.add_node("post", post_gate_node)
    g.add_edge(START, "gate")
    g.add_edge("gate", "post")
    g.add_edge("post", END)

    state = PerpetuaState(session_id="s1")
    result = asyncio.run(g.ainvoke(state))
    assert result.status == "interrupted"
    assert "confirmed" not in result.scratchpad


def test_interrupt_carries_prompt():
    import asyncio
    g = MiniGraph(interrupt_handler="store")
    g.add_node("gate", gating_node)
    g.add_edge(START, "gate")
    g.add_edge("gate", END)

    state = PerpetuaState(session_id="s2")
    result = asyncio.run(g.ainvoke(state))
    assert result.metadata.get("interrupt_prompt") == "Please confirm this action."
    assert result.metadata.get("interrupt_payload") == {"action": "delete"}
