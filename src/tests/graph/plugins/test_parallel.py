import pytest, asyncio
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.plugins.parallel import parallel_dispatch


@pytest.mark.asyncio
async def test_parallel_dispatch_runs_branches_concurrently():
    async def slow(state):
        await asyncio.sleep(0.05)
        return {"scratchpad": {**state.scratchpad, "slow": True}}

    async def fast(state):
        return {"scratchpad": {**state.scratchpad, "fast": True}}

    state = PerpetuaState(session_id="t1")
    result = await parallel_dispatch([("slow", slow), ("fast", fast)], state)
    assert result["scratchpad"]["slow"] is True
    assert result["scratchpad"]["fast"] is True


@pytest.mark.asyncio
async def test_parallel_dispatch_last_writer_wins_on_conflict():
    async def a(state):
        return {"scratchpad": {**state.scratchpad, "k": "from-a"}}

    async def b(state):
        return {"scratchpad": {**state.scratchpad, "k": "from-b"}}

    state = PerpetuaState(session_id="t1")
    result = await parallel_dispatch([("a", a), ("b", b)], state)
    assert result["scratchpad"]["k"] == "from-b"  # b is later in the list
