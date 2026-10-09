"""R3: fan-out regions, reducers and joins run inside the one scheduler."""
from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from perpetua_core.graph.engine import (
    END,
    CompiledGraph,
    FanOut,
    Join,
    MaxStepsExceeded,
    MiniGraph,
    _Settled,
)
from perpetua_core.graph.plugins.interrupts import Interrupt
from perpetua_core.graph.reducers import Reducer, ReducerConflict
from perpetua_core.state import PerpetuaState


def m(text: str | int) -> dict:
    return {"m": text}


def fresh() -> PerpetuaState:
    return PerpetuaState(session_id="r3")


def branch(value: Any, field: str = "messages", delay: float = 0.0):
    async def run(_: PerpetuaState) -> dict:
        await asyncio.sleep(delay)
        return {field: value}

    return run


def failing(exc: Exception, delay: float = 0.0):
    async def run(_: PerpetuaState) -> dict:
        await asyncio.sleep(delay)
        raise exc

    return run


def region(
    branches: dict[str, Any],
    *,
    join: Join | None = None,
    reducers: dict[str, Reducer] | None = None,
    max_steps: int = 20,
) -> MiniGraph:
    graph = MiniGraph(max_steps=max_steps)
    graph.add_node("plan", lambda s: {})
    for name, fn in branches.items():
        graph.add_node(name, fn)
    graph.add_node("after", lambda s: {"scratchpad": {**s.scratchpad, "after": True}})
    graph.set_entry("plan")
    graph.add_edge("plan", FanOut(tuple(branches), "after", join or Join()))
    for field, reducer in (reducers or {}).items():
        graph.add_reducer(field, reducer)
    return graph


async def observe(graph: MiniGraph):
    return [obs async for obs in graph.compile().aobserve(fresh())]


# --- determinism ---------------------------------------------------------


async def run_with_delays(delays: tuple[float, float, float]):
    graph = region(
        {name: branch([m(name)], delay=d) for name, d in zip("abc", delays)},
        reducers={"messages": Reducer("concat")},
    )
    observations = await observe(graph)
    return (
        observations[-1].state.model_dump(),
        [(o.event.kind, o.event.node, o.event.steps) for o in observations],
    )


async def assert_order_independent() -> None:
    baseline = await run_with_delays((0.0, 0.0, 0.0))
    for delays in itertools.permutations((0.0, 0.01, 0.02)):
        assert await run_with_delays(delays) == baseline


async def test_result_and_events_do_not_depend_on_completion_order() -> None:
    await assert_order_independent()
    state, _ = await run_with_delays((0.02, 0.01, 0.0))
    assert state["messages"] == [m("a"), m("b"), m("c")]


async def test_completion_order_merge_is_detected(monkeypatch) -> None:
    """Mutation check: merging in completion order must fail the suite's invariant."""
    original = CompiledGraph._settle

    async def completion_ordered(self, branches, snapshot):
        finished: list[_Settled] = []

        async def call(name):
            out = self._nodes[name](snapshot)
            delta = await out if asyncio.iscoroutine(out) else out
            finished.append(_Settled(name, delta, None))

        await asyncio.gather(*(call(n) for n in branches))
        return finished  # completion order, not canonical order

    monkeypatch.setattr(CompiledGraph, "_settle", completion_ordered)
    with pytest.raises(AssertionError):
        await assert_order_independent()
    monkeypatch.setattr(CompiledGraph, "_settle", original)
    await assert_order_independent()


async def test_branches_share_one_snapshot() -> None:
    seen: dict[str, tuple] = {}

    def make(name):
        def node(s: PerpetuaState) -> dict:
            seen[name] = (tuple(s.nodes_visited), s.status)
            return {}

        return node

    await observe(region({"a": make("a"), "b": make("b")}))
    assert seen["a"] == seen["b"]
    assert seen["a"][0] == ("plan", "a", "b")


async def test_custom_reducer_failure_cannot_mutate_precommit_observations() -> None:
    """A faulty fold must not rewrite already-emitted evidence of the snapshot."""
    def corrupt(base: dict[str, Any], values: tuple[Any, ...]) -> Any:
        base["leaked"] = True
        raise ValueError("faulty fold")

    graph = region({"a": branch({"a": 1}, "scratchpad"),
                    "b": branch({"b": 2}, "scratchpad")},
                   reducers={"scratchpad": Reducer("custom", corrupt)})
    observations = []
    with pytest.raises(ValueError, match="faulty fold"):
        async for observation in graph.compile().aobserve(fresh()):
            observations.append(observation)
    assert not any("leaked" in observation.state.scratchpad for observation in observations)
    assert not any(observation.event.kind == "superstep.commit" for observation in observations)


async def test_event_sequence_and_provenance() -> None:
    observations = await observe(
        region(
            {"b": branch([m("B")]), "a": branch([m("A")])},
            reducers={"messages": Reducer("concat")},
        )
    )
    kinds = [(o.event.kind, o.event.node) for o in observations]
    assert kinds == [
        ("edge.selected", "__start__"),
        ("node.start", "plan"),
        ("node.end", "plan"),
        ("superstep.start", "plan"),
        ("node.start", "a"),
        ("node.start", "b"),
        ("node.end", "a"),
        ("node.end", "b"),
        ("superstep.commit", "plan"),
        ("edge.selected", "plan"),
        ("node.start", "after"),
        ("node.end", "after"),
        ("edge.selected", "after"),
        ("done", None),
    ]
    commit = next(o for o in observations if o.event.kind == "superstep.commit")
    assert commit.event.branches == ("a", "b")
    assert commit.provenance == {"messages": ("a", "b")}
    assert commit.delta == {"messages": [m("A"), m("B")]}
    assert commit.state.nodes_visited == ["plan", "a", "b"]
    assert [o.state.nodes_visited for o in observations if o.event.kind == "node.end"][1] == [
        "plan", "a", "b",
    ]


async def test_graph_continues_after_commit_and_finishes_done() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")])},
        reducers={"messages": Reducer("concat")},
    )
    final = (await observe(graph))[-1].state
    assert final.status == "done"
    assert final.scratchpad == {"after": True}
    assert final.nodes_visited == ["plan", "a", "b", "after"]


# --- reducers ------------------------------------------------------------


async def test_default_rejects_conflict_and_commits_nothing() -> None:
    graph = region({"a": branch("x", "model_hint"), "b": branch("y", "model_hint")})
    seen = []
    with pytest.raises(ReducerConflict) as caught:
        async for obs in graph.compile().aobserve(fresh()):
            seen.append(obs.event.kind)
    assert caught.value.field == "model_hint"
    assert caught.value.branches == ("a", "b")
    assert "superstep.commit" not in seen


async def test_default_accepts_agreeing_writers() -> None:
    graph = region({"a": branch("same", "model_hint"), "b": branch("same", "model_hint")})
    assert (await observe(graph))[-1].state.model_hint == "same"


async def test_single_writer_without_reducer_is_a_replacement() -> None:
    graph = region({"a": branch([m("only")]), "b": branch("hint", "model_hint")})
    final = (await observe(graph))[-1].state
    assert final.messages == [m("only")]
    assert final.model_hint == "hint"


@pytest.mark.parametrize(
    "kind,expected",
    [("first", 1), ("last", 3)],
)
async def test_first_and_last(kind: str, expected: int) -> None:
    graph = region(
        {
            "a": branch(1, "retry_count"),
            "b": branch(2, "retry_count"),
            "c": branch(3, "retry_count"),
        },
        reducers={"retry_count": Reducer(kind)},  # type: ignore[arg-type]
    )
    assert (await observe(graph))[-1].state.retry_count == expected


async def test_concat_appends_to_the_base_in_branch_order() -> None:
    graph = region(
        {"b": branch([m("B")]), "a": branch([m("A")])},
        reducers={"messages": Reducer("concat")},
    )
    state = fresh().merge({"messages": [{"role": "user"}]})
    final = [o async for o in graph.compile().aobserve(state)][-1].state
    assert final.messages == [{"role": "user"}, m("A"), m("B")]


async def test_concat_requires_lists() -> None:
    graph = region(
        {"a": branch("text"), "b": branch([m("B")])}, reducers={"messages": Reducer("concat")}
    )
    with pytest.raises(TypeError, match="requires list values"):
        await observe(graph)


async def test_union_lists_keep_first_occurrence_order() -> None:
    graph = region(
        {"a": branch([m(1), m(2)]), "b": branch([m(2), m(3)])},
        reducers={"messages": Reducer("union")},
    )
    assert (await observe(graph))[-1].state.messages == [m(1), m(2), m(3)]


async def test_union_dicts_merge_and_reject_key_conflicts() -> None:
    ok = region(
        {"a": branch({"x": 1}, "scratchpad"), "b": branch({"y": 2}, "scratchpad")},
        reducers={"scratchpad": Reducer("union")},
    )
    assert (await observe(ok))[-1].state.scratchpad == {"x": 1, "y": 2, "after": True}
    bad = region(
        {"a": branch({"x": 1}, "scratchpad"), "b": branch({"x": 2}, "scratchpad")},
        reducers={"scratchpad": Reducer("union")},
    )
    with pytest.raises(ReducerConflict, match="key 'x'"):
        await observe(bad)


async def test_union_rejects_mixed_types() -> None:
    graph = region(
        {"a": branch({"x": 1}, "scratchpad"), "b": branch([m(1)], "scratchpad")},
        reducers={"scratchpad": Reducer("union")},
    )
    with pytest.raises(TypeError, match="mixes dict and list"):
        await observe(graph)


async def test_custom_reducer_is_a_pure_fold_over_ordered_contributions() -> None:
    def total(base: int, values: tuple[int, ...]) -> int:
        return base + sum(values)

    graph = region(
        {"a": branch(2, "retry_count"), "b": branch(5, "retry_count")},
        reducers={"retry_count": Reducer("custom", total)},
    )
    assert (await observe(graph))[-1].state.retry_count == 7


# --- joins ---------------------------------------------------------------


async def test_all_join_fails_with_exception_group_and_commits_nothing() -> None:
    boom = RuntimeError("boom")
    graph = region({"a": branch([m("A")]), "b": failing(boom)})
    seen = []
    with pytest.raises(ExceptionGroup) as caught:
        async for obs in graph.compile().aobserve(fresh()):
            seen.append(obs.event.kind)
    assert caught.value.exceptions == (boom,)
    assert "superstep.commit" not in seen


async def test_any_join_admits_every_success() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": failing(RuntimeError("x")), "c": branch([m("C")])},
        join=Join("any"),
        reducers={"messages": Reducer("concat")},
    )
    observations = await observe(graph)
    assert observations[-1].state.messages == [m("A"), m("C")]
    commit = next(o for o in observations if o.event.kind == "superstep.commit")
    assert commit.event.branches == ("a", "c")


async def test_any_join_fails_when_nothing_succeeds() -> None:
    graph = region(
        {"a": failing(RuntimeError("1")), "b": failing(ValueError("2"))}, join=Join("any")
    )
    with pytest.raises(ExceptionGroup) as caught:
        await observe(graph)
    assert [type(e) for e in caught.value.exceptions] == [RuntimeError, ValueError]


async def test_first_success_is_lowest_name_not_fastest() -> None:
    graph = region(
        {"a": branch([m("A")], delay=0.03), "b": branch([m("B")], delay=0.0)},
        join=Join("first_success"),
        reducers={"messages": Reducer("concat")},
    )
    assert (await observe(graph))[-1].state.messages == [m("A")]


async def test_first_success_skips_failed_lowest_name() -> None:
    graph = region(
        {"a": failing(RuntimeError("x")), "b": branch([m("B")]), "c": branch([m("C")])},
        join=Join("first_success"),
        reducers={"messages": Reducer("concat")},
    )
    assert (await observe(graph))[-1].state.messages == [m("B")]


async def test_quorum_met_and_unattainable() -> None:
    branches = {"a": branch([m("A")]), "b": failing(RuntimeError("x")), "c": branch([m("C")])}
    met = region(branches, join=Join("quorum", 2), reducers={"messages": Reducer("concat")})
    assert (await observe(met))[-1].state.messages == [m("A"), m("C")]
    unmet = region(branches, join=Join("quorum", 3))
    with pytest.raises(ExceptionGroup):
        await observe(unmet)


async def test_custom_join_admits_a_named_subset_in_canonical_order() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")]), "c": branch([m("C")])},
        join=Join("custom", fn=lambda outcomes: ["c", "a"]),
        reducers={"messages": Reducer("concat")},
    )
    assert (await observe(graph))[-1].state.messages == [m("A"), m("C")]


async def test_custom_join_cannot_admit_a_failed_branch() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": failing(RuntimeError("x"))},
        join=Join("custom", fn=lambda outcomes: ["a", "b"]),
    )
    with pytest.raises(ValueError, match="not a successful branch"):
        await observe(graph)


async def test_custom_join_admitting_nothing_refuses_the_region() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")])},
        join=Join("custom", fn=lambda outcomes: []),
        reducers={"messages": Reducer("concat")},
    )
    with pytest.raises(ExceptionGroup) as caught:
        await observe(graph)
    assert "admitted no branch" in str(caught.value.exceptions[0])


async def test_branch_mutating_its_input_cannot_leak() -> None:
    async def mutator(state: PerpetuaState) -> dict:
        state.scratchpad["leak"] = True
        state.messages.append(m("LEAK"))
        return {"retry_count": 1}

    seen: list = []

    async def reader(state: PerpetuaState) -> dict:
        await asyncio.sleep(0.01)
        seen.append((dict(state.scratchpad), list(state.messages)))
        return {"retry_count": 1}

    graph = region({"a": mutator, "b": reader})
    final = (await observe(graph))[-1].state
    assert seen == [({}, [])]
    assert "leak" not in final.scratchpad
    assert final.messages == []


async def test_custom_join_sees_outcomes_without_deltas() -> None:
    seen: list = []

    def admit(outcomes):
        seen.extend(outcomes)
        return [o.name for o in outcomes if o.ok]

    graph = region(
        {"a": branch([m("A")]), "b": failing(KeyError("k"))}, join=Join("custom", fn=admit)
    )
    await observe(graph)
    assert [(o.name, o.ok, o.error_type) for o in seen] == [("a", True, None), ("b", False, "KeyError")]


async def test_non_dict_branch_result_is_a_branch_failure() -> None:
    graph = region({"a": lambda s: "nope", "b": branch([m("B")])})
    with pytest.raises(ExceptionGroup) as caught:
        await observe(graph)
    assert isinstance(caught.value.exceptions[0], TypeError)


# --- interrupts, cancellation, bounds ------------------------------------


async def test_interrupt_wins_over_failure_and_commits_nothing() -> None:
    graph = region(
        {
            "a": failing(RuntimeError("x")),
            "b": failing(Interrupt("approve?", {"k": 1})),
            "c": branch([m("C")]),
        }
    )
    observations = await observe(graph)
    last = observations[-1]
    assert last.event.kind == "interrupt"
    assert last.event.node == "b"
    assert last.event.terminal_reason == "interrupted"
    assert last.state.status == "interrupted"
    assert last.state.metadata["interrupt_node"] == "b"
    assert last.state.messages == []
    assert "superstep.commit" not in [o.event.kind for o in observations]


async def test_lowest_named_interrupt_is_reported() -> None:
    graph = region(
        {"b": failing(Interrupt("second")), "a": failing(Interrupt("first"))}
    )
    last = (await observe(graph))[-1]
    assert last.state.metadata["interrupt_prompt"] == "first"


async def test_cancelling_the_run_cancels_every_branch() -> None:
    started = asyncio.Event()
    cancelled: list[str] = []

    def hang(name):
        async def run(_: PerpetuaState) -> dict:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(name)
                raise
            return {}

        return run

    graph = region({"a": hang("a"), "b": hang("b")})
    task = asyncio.create_task(graph.ainvoke(fresh()))
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(cancelled) == ["a", "b"]


async def test_region_counts_successful_branches_as_steps() -> None:
    observations = await observe(
        region(
            {"a": branch([m("A")]), "b": branch([m("B")])},
            reducers={"messages": Reducer("concat")},
        )
    )
    commit = next(o for o in observations if o.event.kind == "superstep.commit")
    assert commit.event.steps == 3  # plan + a + b
    assert observations[-2].event.steps == 4  # + after


async def test_region_is_refused_before_running_when_it_would_exceed_max_steps() -> None:
    ran: list[str] = []

    def node(name):
        def run(_: PerpetuaState) -> dict:
            ran.append(name)
            return {}

        return run

    graph = region({"a": node("a"), "b": node("b"), "c": node("c")}, max_steps=3)
    with pytest.raises(MaxStepsExceeded):
        await observe(graph)
    assert ran == []


async def test_bounded_loop_through_a_region() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")])},
        reducers={"messages": Reducer("concat")},
        max_steps=10,
    )
    graph.add_edge("after", "plan")
    with pytest.raises(MaxStepsExceeded):
        await observe(graph)


# --- builder and description ---------------------------------------------


def test_fanout_validation() -> None:
    with pytest.raises(ValueError, match="at least two"):
        FanOut(("a",), "z")
    with pytest.raises(ValueError, match="unique"):
        FanOut(("a", "a"), "z")
    with pytest.raises(ValueError, match="cannot exceed"):
        FanOut(("a", "b"), "z", Join("quorum", 3))
    with pytest.raises(ValueError, match="requires a callable"):
        Join("custom")
    with pytest.raises(ValueError, match="only a quorum"):
        Join("all", 2)
    with pytest.raises(ValueError, match="requires a callable"):
        Reducer("custom")
    assert FanOut(("b", "a"), END).branches == ("a", "b")


def test_start_cannot_fan_out() -> None:
    with pytest.raises(ValueError, match="START cannot fan out"):
        MiniGraph().add_edge("__start__", FanOut(("a", "b"), END))


def test_describe_emits_schema_two_with_reducers_and_joins() -> None:
    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")])},
        join=Join("quorum", 2),
        reducers={"messages": Reducer("concat")},
    )
    spec = graph.describe()
    assert spec.schema_version == "2"
    edge = next(e for e in spec.edges if e.kind == "fanout")
    assert (edge.source, edge.target, edge.declared_targets) == ("plan", "after", ("a", "b"))
    assert [(j.source, j.kind, j.quorum) for j in spec.joins] == [("plan", "quorum", 2)]
    assert [(r.field, r.kind) for r in spec.reducers] == [("messages", "concat")]
    assert graph.compile().describe().graph_id == spec.graph_id
    assert graph.validate().valid


def test_implied_and_explicit_all_join_share_one_graph_id() -> None:
    explicit = region({"a": branch([]), "b": branch([])}, join=Join("all")).describe()
    implied = region({"a": branch([]), "b": branch([])}).describe()
    assert explicit.graph_id == implied.graph_id
    assert implied.joins == ()


def test_branch_authoring_order_does_not_change_graph_id() -> None:
    one = region({"a": branch([]), "b": branch([])}).describe()
    two = region({"b": branch([]), "a": branch([])}).describe()
    assert one.graph_id == two.graph_id


def test_policy_of_the_join_changes_graph_id() -> None:
    base = region({"a": branch([]), "b": branch([])}).describe()
    assert region({"a": branch([]), "b": branch([])}, join=Join("any")).describe().graph_id != base.graph_id
    assert (
        region(
            {"a": branch([]), "b": branch([])}, reducers={"messages": Reducer("concat")}
        ).describe().graph_id
        != base.graph_id
    )


async def test_plugins_receive_commit_provenance() -> None:
    from perpetua_core.graph.plugins.observer import run_with_plugins

    seen: list = []

    class Spy:
        def on_observation(self, observation) -> None:
            if observation.event.kind == "superstep.commit":
                seen.append(observation.provenance)

    graph = region(
        {"a": branch([m("A")]), "b": branch([m("B")])},
        reducers={"messages": Reducer("concat")},
    )
    await run_with_plugins(graph, fresh(), [Spy()])
    assert seen == [{"messages": ("a", "b")}]
