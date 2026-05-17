import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.engine import MiniGraph, END
from perpetua_core.graph.plugins.routing import LabelRouter


@pytest.mark.asyncio
async def test_label_router_dispatches_to_registered_node():
    router = LabelRouter(lambda s: "fast" if s.optimize_for == "speed" else "deep")
    router.register("fast", "fast_node")
    router.register("deep", "deep_node")

    g = MiniGraph()
    g.add_node("entry", lambda s: {"scratchpad": {"hit": "entry"}})
    g.add_node("fast_node", lambda s: {"scratchpad": {**s.scratchpad, "hit": "fast"}})
    g.add_node("deep_node", lambda s: {"scratchpad": {**s.scratchpad, "hit": "deep"}})
    g.set_entry("entry")
    g.add_edge("entry", router.as_edge())
    g.add_edge("fast_node", END)
    g.add_edge("deep_node", END)

    state = await g.ainvoke(PerpetuaState(session_id="t1", optimize_for="speed"))
    assert state.scratchpad["hit"] == "fast"


def test_router_raises_on_unregistered_label():
    router = LabelRouter(lambda s: "unknown")
    with pytest.raises(KeyError, match="unknown"):
        router.as_edge()(PerpetuaState(session_id="t1"))
