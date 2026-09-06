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


def test_invoke_raises_clearly_from_inside_a_running_loop() -> None:
    adapter = LangChainRunnableAdapter(_linear_graph())

    async def call_sync_invoke_from_async_context() -> None:
        adapter.invoke(make_state())

    with pytest.raises(RuntimeError):
        asyncio.run(call_sync_invoke_from_async_context())
