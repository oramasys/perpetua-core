"""Deterministic, side-effect-free static validation for GraphSpec.

The linter reasons only about declared structure. It never executes node
functions or conditional routers and stays conservative when conditional target
sets are opaque.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .engine import END, START
from .spec import (
    GRAPH_SPEC_SCHEMA_VERSION,
    SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS,
    GraphSpec,
    canonical_graph_payload,
    compute_graph_id,
)

LintSeverity: TypeAlias = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class GraphLintIssue:
    code: str
    severity: LintSeverity
    message: str
    node: str | None = None
    source: str | None = None
    target: str | None = None


@dataclass(frozen=True, slots=True)
class GraphValidationReport:
    graph_id: str
    issues: tuple[GraphLintIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class GraphSpecValidationError(ValueError):
    def __init__(self, report: GraphValidationReport) -> None:
        self.report = report
        codes = ", ".join(issue.code for issue in report.issues if issue.severity == "error")
        super().__init__(f"GraphSpec validation failed: {codes or 'unknown error'}")


_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _sort_key(issue: GraphLintIssue) -> tuple[object, ...]:
    return (
        _SEVERITY_RANK[issue.severity],
        issue.code,
        issue.source or "",
        issue.node or "",
        issue.target or "",
        issue.message,
    )


def _issue(
    code: str,
    severity: LintSeverity,
    message: str,
    *,
    node: str | None = None,
    source: str | None = None,
    target: str | None = None,
) -> GraphLintIssue:
    return GraphLintIssue(code, severity, message, node, source, target)


def lint_graph_spec(spec: GraphSpec) -> GraphValidationReport:
    issues: list[GraphLintIssue] = []
    node_names = [node.name for node in spec.nodes]
    node_set = set(node_names)

    if spec.schema_version not in SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS:
        issues.append(
            _issue(
                "GS013",
                "error",
                f"unsupported GraphSpec schema version {spec.schema_version!r}",
            )
        )

    expected_id = compute_graph_id(
        canonical_graph_payload(
            schema_version=spec.schema_version,
            max_steps=spec.max_steps,
            nodes=spec.nodes,
            edges=spec.edges,
            metadata=spec.metadata,
            reducers=spec.reducers,
            joins=spec.joins,
        )
    )
    if spec.graph_id != expected_id:
        issues.append(
            _issue(
                "GS014",
                "error",
                "graph_id does not match canonical structural content",
            )
        )

    seen_nodes: set[str] = set()
    for node in spec.nodes:
        if node.name in seen_nodes:
            issues.append(
                _issue(
                    "GS001",
                    "error",
                    f"duplicate node name {node.name!r}",
                    node=node.name,
                )
            )
        seen_nodes.add(node.name)
        if not isinstance(node.name, str) or not node.name.strip():
            issues.append(
                _issue(
                    "GS002",
                    "error",
                    "node name must be a non-empty string",
                    node=node.name,
                )
            )
        if node.name in {START, END}:
            issues.append(
                _issue(
                    "GS011",
                    "error",
                    f"node name {node.name!r} is reserved",
                    node=node.name,
                )
            )
        if node.implementation_ref is None:
            issues.append(
                _issue(
                    "GS104",
                    "warning",
                    f"node {node.name!r} has no stable implementation reference",
                    node=node.name,
                )
            )

    if spec.max_steps <= 0:
        issues.append(
            _issue("GS010", "error", "max_steps must be positive")
        )

    entry_edges = [edge for edge in spec.edges if edge.source == START]
    if not entry_edges:
        issues.append(
            _issue("GS003", "error", "graph has no START entry edge", source=START)
        )

    seen_sources: set[str] = set()
    for edge in spec.edges:
        if edge.source in seen_sources:
            issues.append(
                _issue(
                    "GS012",
                    "error",
                    f"multiple edges are declared for source {edge.source!r}",
                    source=edge.source,
                )
            )
        seen_sources.add(edge.source)

        if edge.kind == "static":
            if edge.target is None or edge.router_ref is not None or edge.declared_targets:
                issues.append(
                    _issue(
                        "GS008",
                        "error",
                        "invalid static edge shape",
                        source=edge.source,
                        target=edge.target,
                    )
                )
            if edge.source != START and edge.source not in node_set:
                issues.append(
                    _issue(
                        "GS004",
                        "error",
                        f"static edge source {edge.source!r} is not a node",
                        source=edge.source,
                        target=edge.target,
                    )
                )
            if edge.target is not None and edge.target != END and edge.target not in node_set:
                issues.append(
                    _issue(
                        "GS005",
                        "error",
                        f"static edge target {edge.target!r} is not a node",
                        source=edge.source,
                        target=edge.target,
                    )
                )

        elif edge.kind == "conditional":
            if edge.target is not None:
                issues.append(
                    _issue(
                        "GS009",
                        "error",
                        "invalid conditional edge shape",
                        source=edge.source,
                        target=edge.target,
                    )
                )
            if edge.source != START and edge.source not in node_set:
                issues.append(
                    _issue(
                        "GS006",
                        "error",
                        f"conditional edge source {edge.source!r} is not a node",
                        source=edge.source,
                    )
                )
            for target in edge.declared_targets:
                if target != END and target not in node_set:
                    issues.append(
                        _issue(
                            "GS007",
                            "error",
                            f"declared conditional target {target!r} is not a node",
                            source=edge.source,
                            target=target,
                        )
                    )
            if not edge.declared_targets:
                issues.append(
                    _issue(
                        "GS103",
                        "warning",
                        f"conditional edge from {edge.source!r} has no declared targets",
                        source=edge.source,
                    )
                )
            if edge.router_ref is None:
                issues.append(
                    _issue(
                        "GS105",
                        "warning",
                        f"conditional edge from {edge.source!r} has no stable router reference",
                        source=edge.source,
                    )
                )

    issues.extend(_lint_regions(spec, node_set))

    adjacency: dict[str, set[str]] = {}
    opaque_conditional_sources: set[str] = set()
    for edge in spec.edges:
        if edge.kind == "static" and edge.target is not None:
            adjacency.setdefault(edge.source, set()).add(edge.target)
        elif edge.kind == "fanout" and edge.target is not None:
            adjacency.setdefault(edge.source, set()).update(edge.declared_targets)
            adjacency.setdefault(edge.source, set()).add(edge.target)
            for branch in edge.declared_targets:
                adjacency.setdefault(branch, set()).add(edge.target)
        elif edge.kind == "conditional":
            if edge.declared_targets:
                adjacency.setdefault(edge.source, set()).update(edge.declared_targets)
            else:
                opaque_conditional_sources.add(edge.source)

    reachable: set[str] = {START}
    frontier = [START]
    while frontier:
        current = frontier.pop()
        for target in adjacency.get(current, ()):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    reachable_opaque = bool(reachable & opaque_conditional_sources)
    if not reachable_opaque:
        for node_name in sorted(node_set - reachable):
            issues.append(
                _issue(
                    "GS101",
                    "warning",
                    f"node {node_name!r} is definitely unreachable from START",
                    node=node_name,
                )
            )

    if END not in reachable and not reachable_opaque:
        issues.append(
            _issue(
                "GS102",
                "warning",
                "known graph structure has no definite path from START to END",
            )
        )

    return GraphValidationReport(
        graph_id=spec.graph_id,
        issues=tuple(sorted(issues, key=_sort_key)),
    )


def _lint_regions(spec: GraphSpec, node_set: set[str]) -> list[GraphLintIssue]:
    """Execution-free checks for fan-out regions, reducers and joins (GS2xx)."""
    issues: list[GraphLintIssue] = []
    fanouts = [edge for edge in spec.edges if edge.kind == "fanout"]
    uses_r3 = bool(fanouts or spec.reducers or spec.joins)
    own_edge_sources = {edge.source for edge in spec.edges}

    if uses_r3 and spec.schema_version == GRAPH_SPEC_SCHEMA_VERSION:
        issues.append(
            _issue(
                "GS211",
                "error",
                "fan-out edges, reducers and joins require GraphSpec schema version '2'",
            )
        )
    if (
        not uses_r3
        and spec.schema_version in SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS
        and spec.schema_version != GRAPH_SPEC_SCHEMA_VERSION
    ):
        issues.append(
            _issue(
                "GS212",
                "error",
                "GraphSpec schema version '2' requires a fan-out edge, reducer or join",
            )
        )

    claimed: dict[str, str] = {}
    for edge in fanouts:
        if edge.source not in node_set:
            issues.append(
                _issue(
                    "GS201",
                    "error",
                    f"fan-out source {edge.source!r} is not a node",
                    source=edge.source,
                )
            )
        if edge.target is not None and edge.target != END and edge.target not in node_set:
            issues.append(
                _issue(
                    "GS205",
                    "error",
                    f"fan-out target {edge.target!r} is not a node",
                    source=edge.source,
                    target=edge.target,
                )
            )
        for branch in edge.declared_targets:
            if branch not in node_set:
                issues.append(
                    _issue(
                        "GS202",
                        "error",
                        f"fan-out branch {branch!r} is not a node",
                        source=edge.source,
                        target=branch,
                    )
                )
                continue
            if branch in {edge.source, edge.target, START, END}:
                issues.append(
                    _issue(
                        "GS203",
                        "error",
                        f"fan-out branch {branch!r} cannot be the source, target or a sentinel",
                        source=edge.source,
                        target=branch,
                    )
                )
            if branch in own_edge_sources:
                issues.append(
                    _issue(
                        "GS204",
                        "error",
                        f"fan-out branch {branch!r} declares its own edge, "
                        "which a region ignores",
                        source=edge.source,
                        target=branch,
                    )
                )
            if branch in claimed and claimed[branch] != edge.source:
                issues.append(
                    _issue(
                        "GS206",
                        "error",
                        f"node {branch!r} is a branch of more than one region",
                        source=edge.source,
                        target=branch,
                    )
                )
            claimed.setdefault(branch, edge.source)

    branch_counts = {edge.source: len(edge.declared_targets) for edge in fanouts}
    seen_joins: set[str] = set()
    for join in spec.joins:
        if join.source not in branch_counts:
            issues.append(
                _issue(
                    "GS207",
                    "error",
                    f"join source {join.source!r} is not a fan-out source",
                    source=join.source,
                )
            )
        if join.source in seen_joins:
            issues.append(
                _issue(
                    "GS208",
                    "error",
                    f"multiple joins are declared for source {join.source!r}",
                    source=join.source,
                )
            )
        seen_joins.add(join.source)
        if (
            join.kind == "quorum"
            and join.quorum is not None
            and join.source in branch_counts
            and join.quorum > branch_counts[join.source]
        ):
            issues.append(
                _issue(
                    "GS209",
                    "error",
                    f"quorum {join.quorum} exceeds the {branch_counts[join.source]} "
                    f"branches of {join.source!r}",
                    source=join.source,
                )
            )
        if join.kind == "custom" and join.ref is None:
            issues.append(
                _issue(
                    "GS213",
                    "warning",
                    f"custom join for {join.source!r} has no stable function reference",
                    source=join.source,
                )
            )

    seen_fields: set[str] = set()
    for reducer in spec.reducers:
        if reducer.field in seen_fields:
            issues.append(
                _issue(
                    "GS210",
                    "error",
                    f"multiple reducers are declared for field {reducer.field!r}",
                    node=reducer.field,
                )
            )
        seen_fields.add(reducer.field)
        if reducer.kind == "custom" and reducer.ref is None:
            issues.append(
                _issue(
                    "GS213",
                    "warning",
                    f"custom reducer for {reducer.field!r} has no stable function reference",
                    node=reducer.field,
                )
            )
    if spec.reducers and not fanouts:
        issues.append(
            _issue(
                "GS214",
                "warning",
                "reducers are declared but the graph has no fan-out region",
            )
        )
    return issues


def validate_graph_spec(spec: GraphSpec) -> None:
    report = lint_graph_spec(spec)
    if not report.valid:
        raise GraphSpecValidationError(report)
