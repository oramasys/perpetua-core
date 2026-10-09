"""GraphSpec schema 2: fan-out edges, reducers, joins, and v1 compatibility."""
from __future__ import annotations

import json

import pytest

from perpetua_core.graph.lint import lint_graph_spec
from perpetua_core.graph.spec import (
    EdgeSpec,
    GraphSpec,
    JoinSpec,
    NodeSpec,
    ReducerSpec,
    compute_graph_id,
)

# graph_id of the schema-1 reference spec below, computed before R3 existed.
V1_GOLDEN_GRAPH_ID = "d8aeff4811d864972d52d7e03a14a07b5ea9ddcb39f6dec42fbd5c1ca950db2d"


def v1_spec() -> GraphSpec:
    return GraphSpec.create(
        max_steps=20,
        nodes=(
            NodeSpec("a", implementation_ref="tests:a"),
            NodeSpec("b", implementation_ref="tests:b"),
        ),
        edges=(
            EdgeSpec("__start__", "static", target="a"),
            EdgeSpec("a", "static", target="b"),
            EdgeSpec("b", "static", target="__end__"),
        ),
        metadata={"owner": "core"},
    )


def region_spec(**overrides) -> GraphSpec:
    kwargs = dict(
        max_steps=20,
        nodes=tuple(NodeSpec(n, implementation_ref=f"tests:{n}") for n in ("plan", "a", "b", "z")),
        edges=(
            EdgeSpec("__start__", "static", target="plan"),
            EdgeSpec("plan", "fanout", target="z", declared_targets=("b", "a")),
            EdgeSpec("z", "static", target="__end__"),
        ),
        reducers=(ReducerSpec("messages", "concat"),),
        joins=(JoinSpec("plan", "quorum", quorum=2),),
    )
    kwargs.update(overrides)
    return GraphSpec.create(**kwargs)


def codes(spec: GraphSpec) -> set[str]:
    return {issue.code for issue in lint_graph_spec(spec).issues}


# --- v1 compatibility ----------------------------------------------------


def test_schema_one_graph_id_and_payload_are_unchanged() -> None:
    spec = v1_spec()
    assert spec.schema_version == "1"
    assert spec.graph_id == V1_GOLDEN_GRAPH_ID
    payload = json.loads(spec.canonical_json())
    assert "reducers" not in payload and "joins" not in payload
    assert GraphSpec.from_json(spec.to_json()) == spec


def test_schema_one_rejects_r3_features_and_schema_two_requires_them() -> None:
    with pytest.raises(ValueError, match="require GraphSpec schema version '2'"):
        region_spec(schema_version="1")
    with pytest.raises(ValueError, match="requires a fan-out edge, reducer or join"):
        GraphSpec.create(max_steps=3, nodes=(NodeSpec("a"),), schema_version="2")


# --- schema 2 ------------------------------------------------------------


def test_schema_two_round_trips_and_is_deterministic() -> None:
    spec = region_spec()
    assert spec.schema_version == "2"
    assert spec.reducers == (ReducerSpec("messages", "concat"),)
    assert spec.joins == (JoinSpec("plan", "quorum", quorum=2),)
    assert GraphSpec.from_json(spec.to_json()) == spec
    assert region_spec().graph_id == spec.graph_id


def test_reducers_and_joins_are_part_of_graph_id() -> None:
    base = region_spec()
    assert region_spec(reducers=(ReducerSpec("messages", "union"),)).graph_id != base.graph_id
    assert region_spec(joins=(JoinSpec("plan", "any"),)).graph_id != base.graph_id
    assert region_spec(reducers=()).graph_id != base.graph_id


def test_default_all_join_is_dropped_before_hashing() -> None:
    assert region_spec(joins=(JoinSpec("plan", "all"),)).graph_id == region_spec(joins=()).graph_id
    assert region_spec(joins=(JoinSpec("plan", "all"),)).joins == ()


def test_tampered_graph_id_or_content_is_rejected() -> None:
    payload = json.loads(region_spec().to_json())
    payload["reducers"][0]["kind"] = "last"
    with pytest.raises(ValueError, match="graph_id mismatch"):
        GraphSpec.from_dict(payload)


def test_schema_one_payload_with_reducers_is_rejected() -> None:
    payload = v1_spec().to_dict()
    payload["reducers"] = [{"field": "messages", "kind": "concat", "ref": None}]
    with pytest.raises(ValueError):
        GraphSpec.from_dict(payload)


def test_unknown_schema_version_is_still_rejected() -> None:
    payload = region_spec().to_dict()
    payload["schema_version"] = "3"
    with pytest.raises(ValueError, match="unsupported GraphSpec schema version"):
        GraphSpec.from_dict(payload)


def test_spec_records_are_shape_checked() -> None:
    with pytest.raises(ValueError, match="unsupported reducer kind"):
        ReducerSpec("f", "sum")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only a custom reducer"):
        ReducerSpec("f", "first", ref="m:f")
    with pytest.raises(ValueError, match="quorum >= 1"):
        JoinSpec("s", "quorum")
    with pytest.raises(ValueError, match="quorum >= 1"):
        JoinSpec("s", "quorum", quorum=0)
    with pytest.raises(ValueError, match="only a quorum"):
        JoinSpec("s", "any", quorum=2)
    with pytest.raises(ValueError, match="only a custom join"):
        JoinSpec("s", "all", ref="m:f")
    with pytest.raises(ValueError, match="unsupported join kind"):
        JoinSpec("s", "race")  # type: ignore[arg-type]


def test_fanout_edge_shape() -> None:
    with pytest.raises(ValueError, match="requires target"):
        EdgeSpec("p", "fanout", declared_targets=("a", "b"))
    with pytest.raises(ValueError, match="router_ref"):
        EdgeSpec("p", "fanout", target="z", router_ref="m:r", declared_targets=("a", "b"))
    with pytest.raises(ValueError, match="at least two"):
        EdgeSpec("p", "fanout", target="z", declared_targets=("a",))
    with pytest.raises(ValueError, match="unique"):
        EdgeSpec("p", "fanout", target="z", declared_targets=("a", "a"))


# --- lint ----------------------------------------------------------------


def test_valid_region_lints_clean_and_branches_are_reachable() -> None:
    report = lint_graph_spec(region_spec())
    assert report.valid
    assert not [i for i in report.issues if i.code in {"GS101", "GS102"}]


def bypass(spec: GraphSpec, **changes) -> GraphSpec:
    """Build a structurally invalid spec directly, as untrusted input could."""
    values = {
        "schema_version": spec.schema_version,
        "graph_id": spec.graph_id,
        "max_steps": spec.max_steps,
        "nodes": spec.nodes,
        "edges": spec.edges,
        "metadata": spec.metadata,
        "reducers": spec.reducers,
        "joins": spec.joins,
    }
    values.update(changes)
    return GraphSpec(**values)


def test_lint_flags_region_shape_problems() -> None:
    spec = region_spec()
    fan = lambda **kw: EdgeSpec(  # noqa: E731
        "plan", "fanout", **{"target": "z", "declared_targets": ("a", "b"), **kw}
    )
    start = EdgeSpec("__start__", "static", target="plan")
    end = EdgeSpec("z", "static", target="__end__")

    def with_edges(*edges: EdgeSpec) -> set[str]:
        return codes(bypass(spec, edges=(start, end, *edges)))

    assert "GS201" in codes(
        bypass(spec, edges=(start, end, EdgeSpec("ghost", "fanout", target="z", declared_targets=("a", "b"))))
    )
    assert "GS202" in with_edges(fan(declared_targets=("a", "ghost")))
    assert "GS203" in with_edges(fan(declared_targets=("a", "plan")))
    assert "GS203" in with_edges(fan(declared_targets=("a", "z")))
    assert "GS204" in with_edges(fan(), EdgeSpec("a", "static", target="z"))
    assert "GS205" in with_edges(fan(target="ghost"))
    assert "GS206" in codes(
        bypass(
            spec,
            edges=(start, end, fan(), EdgeSpec("z", "fanout", target="__end__", declared_targets=("a", "b"))),
        )
    )


def test_lint_flags_join_and_reducer_problems() -> None:
    spec = region_spec()
    assert "GS207" in codes(bypass(spec, joins=(JoinSpec("z", "any"),)))
    assert "GS208" in codes(bypass(spec, joins=(JoinSpec("plan", "any"), JoinSpec("plan", "quorum", quorum=2))))
    assert "GS209" in codes(bypass(spec, joins=(JoinSpec("plan", "quorum", quorum=3),)))
    assert "GS210" in codes(
        bypass(spec, reducers=(ReducerSpec("messages", "concat"), ReducerSpec("messages", "last")))
    )
    assert "GS213" in codes(bypass(spec, joins=(JoinSpec("plan", "custom"),)))
    assert "GS213" in codes(bypass(spec, reducers=(ReducerSpec("messages", "custom"),)))
    no_fan = bypass(
        spec,
        edges=(EdgeSpec("__start__", "static", target="plan"), EdgeSpec("plan", "static", target="__end__")),
        joins=(),
    )
    assert "GS214" in codes(no_fan)


def test_lint_flags_schema_feature_mismatch_and_hash_binding() -> None:
    spec = region_spec()
    assert "GS211" in codes(bypass(spec, schema_version="1"))
    plain = v1_spec()
    assert "GS212" in codes(bypass(plain, schema_version="2"))
    wrong = bypass(spec, reducers=(ReducerSpec("messages", "last"),))
    assert "GS014" in codes(wrong)
    assert compute_graph_id  # hash binding covered via GS014 above


def test_invalid_default_joins_are_kept_so_lint_can_report_them() -> None:
    spec = region_spec(joins=(JoinSpec("ghost", "all"),))
    assert any(j.source == "ghost" for j in spec.joins)
    assert "GS207" in codes(spec)
    dup = region_spec(joins=(JoinSpec("plan", "all"), JoinSpec("plan", "any")))
    assert len([j for j in dup.joins if j.source == "plan"]) == 2
    assert "GS208" in codes(dup)


def test_sole_default_join_on_a_fanout_source_is_still_dropped() -> None:
    assert region_spec(joins=(JoinSpec("plan", "all"),)).graph_id == region_spec(joins=()).graph_id


@pytest.mark.parametrize("record", ["graph", "nodes", "edges", "reducers", "joins"])
def test_loaded_spec_rejects_unhashed_unknown_fields(record: str) -> None:
    """A description must not silently discard semantics outside its content hash."""
    payload = region_spec().to_dict()
    target = payload if record == "graph" else payload[record][0]
    target["unrecognized_semantics"] = True
    with pytest.raises(ValueError, match="unknown.*field"):
        GraphSpec.from_dict(payload)
