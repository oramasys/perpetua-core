"""Tests for LangChainRunnableAdapter against the real MiniGraph surface."""
from __future__ import annotations

import asyncio

import pytest

from perpetua_core.graph.adapters.langchain_adapter import ChainedRunnable, LangChainRunnableAdapter
from perpetua_core.graph.engine import END, START, MiniGraph
from perpetua_core.state import PerpetuaState


def make_state(**kwargs: object) -> PerpetuaState:
    kwargs.setdefault("session_id", "test")
    return PerpetuaState(**kwargs)


def _linear_graph() -> MiniGraph:
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
    return g


def test_ainvoke_runs_to_completion() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    result = asyncio.run(adapter.ainvoke(make_state()))

    assert result.scratchpad == {"a": True, "b": True}


def test_ainvoke_accepts_plain_dict_input() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    result = asyncio.run(adapter.ainvoke({"session_id": "dict-input"}))

    assert result.session_id == "dict-input"
    assert result.scratchpad == {"a": True, "b": True}


def test_invoke_bridges_sync_callers() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    result = adapter.invoke(make_state())

    assert result.scratchpad == {"a": True, "b": True}


def test_batch_runs_all_inputs() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    results = adapter.batch([make_state(session_id="s1"), make_state(session_id="s2")])

    assert {r.session_id for r in results} == {"s1", "s2"}
    assert all(r.scratchpad == {"a": True, "b": True} for r in results)


def test_astream_yields_a_snapshot_per_completed_node() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    async def collect() -> list[PerpetuaState]:
        return [snapshot async for snapshot in adapter.astream(make_state())]

    snapshots = asyncio.run(collect())

    assert len(snapshots) == 2
    assert snapshots[0].scratchpad == {"a": True}
    assert snapshots[1].scratchpad == {"a": True, "b": True}


def test_stream_bridges_sync_callers_and_matches_astream() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    snapshots = list(adapter.stream(make_state()))

    assert len(snapshots) == 2
    assert snapshots[-1].scratchpad == {"a": True, "b": True}


def test_pipe_operator_chains_two_runnables() -> None:
    first = LangChainRunnableAdapter(_linear_graph())

    def make_second_graph() -> MiniGraph:
        def node_c(state: PerpetuaState) -> dict:
            return {"scratchpad": {**state.scratchpad, "c": True}}

        g = MiniGraph()
        g.add_node("c", node_c)
        g.add_edge(START, "c")
        g.add_edge("c", END)
        return g

    second = LangChainRunnableAdapter(make_second_graph())
    chained = first | second

    assert isinstance(chained, ChainedRunnable)
    result = chained.invoke(make_state())
    assert result.scratchpad == {"a": True, "b": True, "c": True}


@pytest.mark.filterwarnings("error")
def test_invoke_raises_clearly_from_inside_a_running_loop() -> None:
    """Regression test: invoke()/batch()/stream() must check for a running
    loop BEFORE creating their coroutine, not after -- otherwise a rejected
    call leaves an unawaited coroutine behind, surfacing as a spurious
    'coroutine was never awaited' RuntimeWarning (caught here by promoting
    warnings to errors: the test fails if that warning reappears)."""
    adapter = LangChainRunnableAdapter(_linear_graph())

    async def call_sync_invoke_from_async_context() -> None:
        adapter.invoke(make_state())

    with pytest.raises(RuntimeError):
        asyncio.run(call_sync_invoke_from_async_context())


@pytest.mark.filterwarnings("error")
def test_batch_raises_clearly_from_inside_a_running_loop() -> None:
    """CodeRabbit finding: the running-loop guard test previously covered
    only invoke() -- extend the same coverage to batch()."""
    adapter = LangChainRunnableAdapter(_linear_graph())

    async def call_sync_batch_from_async_context() -> None:
        adapter.batch([make_state()])

    with pytest.raises(RuntimeError):
        asyncio.run(call_sync_batch_from_async_context())


@pytest.mark.filterwarnings("error")
def test_stream_raises_clearly_from_inside_a_running_loop() -> None:
    """CodeRabbit finding: the running-loop guard test previously covered
    only invoke() -- extend the same coverage to stream()."""
    adapter = LangChainRunnableAdapter(_linear_graph())

    async def call_sync_stream_from_async_context() -> None:
        list(adapter.stream(make_state()))

    with pytest.raises(RuntimeError):
        asyncio.run(call_sync_stream_from_async_context())


def test_abatch_honors_max_concurrency() -> None:
    """CodeRabbit finding: abatch() silently ignored
    RunnableConfig['max_concurrency'], always running every input fully
    concurrently via asyncio.gather. This proves it actually bounds the
    number of in-flight graph runs, not just that it accepts the key."""
    peak_concurrent = 0
    current_concurrent = 0

    def node_track_concurrency(state: PerpetuaState) -> dict:
        # Synchronous nodes can't yield control mid-run, so concurrency is
        # observed at the graph-run boundary via a wrapping async node
        # instead -- see the ainvoke override below.
        return {}

    class _TrackedAdapter(LangChainRunnableAdapter):
        async def ainvoke(self, input_data, config=None):  # type: ignore[override]
            nonlocal peak_concurrent, current_concurrent
            current_concurrent += 1
            peak_concurrent = max(peak_concurrent, current_concurrent)
            try:
                await asyncio.sleep(0.01)
                return await super().ainvoke(input_data, config=config)
            finally:
                current_concurrent -= 1

    g = MiniGraph()
    g.add_node("a", node_track_concurrency)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    adapter = _TrackedAdapter(g)

    inputs = [make_state(session_id=f"s{i}") for i in range(6)]
    results = asyncio.run(adapter.abatch(inputs, config={"max_concurrency": 2}))

    assert len(results) == 6
    assert peak_concurrent <= 2, f"expected at most 2 concurrent runs, saw {peak_concurrent}"


def test_abatch_without_max_concurrency_runs_fully_concurrent() -> None:
    """Omitting max_concurrency must preserve the prior behavior exactly --
    no artificial bound when the caller didn't ask for one."""
    peak_concurrent = 0
    current_concurrent = 0

    class _TrackedAdapter(LangChainRunnableAdapter):
        async def ainvoke(self, input_data, config=None):  # type: ignore[override]
            nonlocal peak_concurrent, current_concurrent
            current_concurrent += 1
            peak_concurrent = max(peak_concurrent, current_concurrent)
            try:
                await asyncio.sleep(0.01)
                return await super().ainvoke(input_data, config=config)
            finally:
                current_concurrent -= 1

    adapter = _TrackedAdapter(_linear_graph())
    inputs = [make_state(session_id=f"s{i}") for i in range(6)]

    results = asyncio.run(adapter.abatch(inputs))

    assert len(results) == 6
    assert peak_concurrent == 6
