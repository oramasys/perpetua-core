from __future__ import annotations

import pytest

from perpetua_core.graph.engine import END, START
from perpetua_core.graph.lint import GraphSpecValidationError, lint_graph_spec, validate_graph_spec
from perpetua_core.graph.spec import EdgeSpec, GraphSpec, NodeSpec


def _node(name: str) -> NodeSpec:
    return NodeSpec(name, implementation_ref=f"tests:{name}")


def _codes(spec: GraphSpec) -> list[str]:
    return [issue.code for issue in lint_graph_spec(spec).issues]


def test_lint_rejects_missing_entry_edge() -> None:
    spec = GraphSpec.create(max_steps=10, nodes=(_node("a"),), edges=())
    assert "GS003" in _codes(spec)


def test_lint_rejects_unknown_static_target() -> None:
    spec = GraphSpec.create(
        max_steps=10,
        nodes=(_node("a"),),
        edges=(
            EdgeSpec(START, "static", target="a"),
            EdgeSpec("a", "static", target="missing"),
        ),
    )
    report = lint_graph_spec(spec)
    assert not report.valid
    assert "GS005" in [issue.code for issue in report.issues]


def test_lint_rejects_unknown_declared_conditional_target() -> None:
    spec = GraphSpec.create(
        max_steps=10,
        nodes=(_node("a"),),
        edges=(
            EdgeSpec(START, "static", target="a"),
            EdgeSpec("a", "conditional", declared_targets=("missing",), router_ref="tests:route"),
        ),
    )
    assert "GS007" in _codes(spec)


def test_lint_rejects_non_positive_max_steps() -> None:
    spec = GraphSpec.create(
        max_steps=0,
        nodes=(_node("a"),),
        edges=(EdgeSpec(START, "static", target="a"), EdgeSpec("a", "static", target=END)),
    )
    assert "GS010" in _codes(spec)


def test_opaque_reachable_conditional_is_conservative_about_unreachable_nodes() -> None:
    spec = GraphSpec.create(
        max_steps=10,
        nodes=(_node("route"), _node("possible")),
        edges=(
            EdgeSpec(START, "static", target="route"),
            EdgeSpec("route", "conditional", router_ref="tests:route"),
        ),
    )
    codes = _codes(spec)
    assert "GS103" in codes
    assert "GS101" not in codes
    assert "GS102" not in codes


def test_declared_targets_enable_definite_reachability() -> None:
    spec = GraphSpec.create(
        max_steps=10,
        nodes=(_node("route"), _node("fast"), _node("dead")),
        edges=(
            EdgeSpec(START, "static", target="route"),
            EdgeSpec("route", "conditional", router_ref="tests:route", declared_targets=("fast",)),
            EdgeSpec("fast", "static", target=END),
        ),
    )
    issues = lint_graph_spec(spec).issues
    assert any(issue.code == "GS101" and issue.node == "dead" for issue in issues)
    assert "GS102" not in [issue.code for issue in issues]


def test_issue_order_is_deterministic() -> None:
    spec = GraphSpec.create(max_steps=0, nodes=(_node("dead"),), edges=())
    first = lint_graph_spec(spec).issues
    second = lint_graph_spec(spec).issues
    assert first == second


def test_validate_raises_with_report_for_structural_error() -> None:
    spec = GraphSpec.create(max_steps=10, nodes=(_node("a"),), edges=())
    with pytest.raises(GraphSpecValidationError) as exc:
        validate_graph_spec(spec)
    assert not exc.value.report.valid
