"""Immutable, versioned structural descriptions for Perpetua Core graphs.

GraphSpec is data, not a scheduler. It serializes topology and stable provenance
only; node/router code is never serialized or executed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias

GRAPH_SPEC_SCHEMA_VERSION = "1"
# Schema "2" is used only by specs that declare fan-out regions, reducers or
# joins. Specs that use none of them stay schema "1" with an unchanged graph_id.
GRAPH_SPEC_SCHEMA_VERSION_R3 = "2"
SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS = frozenset(
    {GRAPH_SPEC_SCHEMA_VERSION, GRAPH_SPEC_SCHEMA_VERSION_R3}
)
EdgeKind = Literal["static", "conditional", "fanout"]
ReducerKind = Literal[
    "reject_conflict", "first", "last", "concat", "union", "custom"
]
JoinKind = Literal["all", "any", "first_success", "quorum", "custom"]
REDUCER_KINDS: tuple[str, ...] = (
    "reject_conflict", "first", "last", "concat", "union", "custom",
)
JOIN_KINDS: tuple[str, ...] = ("all", "any", "first_success", "quorum", "custom")
JSONScalar: TypeAlias = None | bool | int | float | str
FrozenJSON: TypeAlias = JSONScalar | tuple["FrozenJSON", ...] | Mapping[str, "FrozenJSON"]


def _freeze_json(value: Any) -> FrozenJSON:
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("GraphSpec metadata floats must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJSON] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("GraphSpec metadata keys must be strings")
            frozen[key] = _freeze_json(value[key])
        return MappingProxyType(frozen)
    raise TypeError(
        "GraphSpec metadata must be JSON-compatible; "
        f"got {type(value).__name__}"
    )


def _thaw_json(value: FrozenJSON) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(value[key]) for key in sorted(value)}
    return value


def stable_callable_ref(value: object) -> str | None:
    """Return a process-stable ``module:qualname`` when one exists."""

    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return None
    if "<locals>" in qualname or "<lambda>" in qualname:
        return None
    return f"{module}:{qualname}"


@dataclass(frozen=True, slots=True)
class NodeSpec:
    name: str
    implementation_ref: str | None = None
    metadata: Mapping[str, FrozenJSON] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_json(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "implementation_ref": self.implementation_ref,
            "metadata": _thaw_json(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    source: str
    kind: EdgeKind
    target: str | None = None
    router_ref: str | None = None
    declared_targets: tuple[str, ...] = ()
    metadata: Mapping[str, FrozenJSON] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.kind == "static":
            if self.target is None:
                raise ValueError("static edge requires target")
            if self.router_ref is not None or self.declared_targets:
                raise ValueError(
                    "static edge must not set router_ref or declared_targets"
                )
        elif self.kind == "conditional":
            if self.target is not None:
                raise ValueError("conditional edge must not set target")
        elif self.kind == "fanout":
            # target is the node (or END) that runs after the region commits;
            # declared_targets are the branches and are authoritative here.
            if self.target is None:
                raise ValueError("fanout edge requires target")
            if self.router_ref is not None:
                raise ValueError("fanout edge must not set router_ref")
            if len(set(self.declared_targets)) != len(self.declared_targets):
                raise ValueError("fanout edge branches must be unique")
            if len(self.declared_targets) < 2:
                raise ValueError("fanout edge requires at least two branches")
        else:
            raise ValueError(f"unsupported edge kind: {self.kind!r}")
        object.__setattr__(
            self,
            "declared_targets",
            tuple(sorted(set(self.declared_targets))),
        )
        object.__setattr__(self, "metadata", _freeze_json(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "target": self.target,
            "router_ref": self.router_ref,
            "declared_targets": list(self.declared_targets),
            "metadata": _thaw_json(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ReducerSpec:
    """How concurrent branch writes to one state field are folded."""

    field: str
    kind: ReducerKind
    ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not self.field:
            raise ValueError("reducer field must be a non-empty string")
        if self.kind not in REDUCER_KINDS:
            raise ValueError(f"unsupported reducer kind: {self.kind!r}")
        if self.kind != "custom" and self.ref is not None:
            raise ValueError("only a custom reducer may set ref")

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "kind": self.kind, "ref": self.ref}


@dataclass(frozen=True, slots=True)
class JoinSpec:
    """Which settled branches of one fan-out region are admitted."""

    source: str
    kind: JoinKind = "all"
    quorum: int | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in JOIN_KINDS:
            raise ValueError(f"unsupported join kind: {self.kind!r}")
        if self.kind == "quorum":
            if isinstance(self.quorum, bool) or not isinstance(self.quorum, int) or self.quorum < 1:
                raise ValueError("quorum join requires an integer quorum >= 1")
        elif self.quorum is not None:
            raise ValueError("only a quorum join may set quorum")
        if self.kind != "custom" and self.ref is not None:
            raise ValueError("only a custom join may set ref")

    @property
    def is_default(self) -> bool:
        """``all`` with no parameters is the implied join of every region."""
        return self.kind == "all"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "quorum": self.quorum,
            "ref": self.ref,
        }


def _node_sort_key(node: NodeSpec) -> tuple[str, str, str]:
    return node.name, node.implementation_ref or "", _canonical_json(_thaw_json(node.metadata))


def _edge_sort_key(edge: EdgeSpec) -> tuple[Any, ...]:
    return (
        edge.source,
        edge.kind,
        edge.target or "",
        edge.router_ref or "",
        edge.declared_targets,
        _canonical_json(_thaw_json(edge.metadata)),
    )


def canonical_graph_payload(
    *,
    schema_version: str,
    max_steps: int,
    nodes: tuple[NodeSpec, ...],
    edges: tuple[EdgeSpec, ...],
    metadata: Mapping[str, FrozenJSON],
    reducers: tuple[ReducerSpec, ...] = (),
    joins: tuple[JoinSpec, ...] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "max_steps": max_steps,
        "nodes": [node.to_dict() for node in sorted(nodes, key=_node_sort_key)],
        "edges": [edge.to_dict() for edge in sorted(edges, key=_edge_sort_key)],
        "metadata": _thaw_json(metadata),
    }
    # The schema "1" payload is byte-identical to releases before R3.
    if schema_version != GRAPH_SPEC_SCHEMA_VERSION:
        payload["reducers"] = [
            r.to_dict() for r in sorted(reducers, key=lambda r: r.field)
        ]
        payload["joins"] = [j.to_dict() for j in sorted(joins, key=lambda j: j.source)]
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_graph_id(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _check_record_fields(payload: Mapping[str, Any], allowed: set[str], record: str) -> None:
    """Reject unhashed semantics; annotations belong inside the metadata object."""
    if not isinstance(payload, Mapping):
        raise TypeError(f"GraphSpec {record} must be an object")
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"GraphSpec {record} has unknown fields: {sorted(unknown, key=str)!r}")


@dataclass(frozen=True, slots=True)
class GraphSpec:
    schema_version: str
    graph_id: str
    max_steps: int
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    metadata: Mapping[str, FrozenJSON] = field(
        default_factory=lambda: MappingProxyType({})
    )
    reducers: tuple[ReducerSpec, ...] = ()
    joins: tuple[JoinSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes, key=_node_sort_key)))
        object.__setattr__(self, "edges", tuple(sorted(self.edges, key=_edge_sort_key)))
        object.__setattr__(self, "metadata", _freeze_json(dict(self.metadata)))
        object.__setattr__(
            self, "reducers", tuple(sorted(self.reducers, key=lambda r: r.field))
        )
        object.__setattr__(
            self, "joins", tuple(sorted(self.joins, key=lambda j: j.source))
        )

    @classmethod
    def create(
        cls,
        *,
        max_steps: int,
        nodes: tuple[NodeSpec, ...] = (),
        edges: tuple[EdgeSpec, ...] = (),
        metadata: Mapping[str, Any] | None = None,
        schema_version: str | None = None,
        reducers: tuple[ReducerSpec, ...] = (),
        joins: tuple[JoinSpec, ...] = (),
    ) -> "GraphSpec":
        """Build a spec; the schema version follows from the features used.

        Fan-out edges, reducers and joins require schema "2". A spec that uses
        none of them is schema "1", so its ``graph_id`` is unchanged. The
        implied default join (``all``, sole join of a fan-out source) is dropped so
        one meaning has one hash; invalid declarations are kept for lint.
        """
        frozen_metadata = _freeze_json(dict(metadata or {}))
        if not isinstance(frozen_metadata, Mapping):
            raise TypeError("GraphSpec metadata must be a mapping")
        canonical_nodes = tuple(sorted(nodes, key=_node_sort_key))
        canonical_edges = tuple(sorted(edges, key=_edge_sort_key))
        canonical_reducers = tuple(sorted(reducers, key=lambda r: r.field))
        fanout_sources = {e.source for e in canonical_edges if e.kind == "fanout"}
        join_counts: dict[str, int] = {}
        for j in joins:
            join_counts[j.source] = join_counts.get(j.source, 0) + 1
        # Drop only a valid implied default: the sole join of a real fan-out
        # source. Orphan or duplicate declarations stay so lint can report them.
        canonical_joins = tuple(
            sorted(
                (
                    j
                    for j in joins
                    if not (
                        j.is_default
                        and j.source in fanout_sources
                        and join_counts[j.source] == 1
                    )
                ),
                key=lambda j: (j.source, j.kind),
            )
        )
        uses_r3 = bool(
            canonical_reducers
            or canonical_joins
            or any(edge.kind == "fanout" for edge in canonical_edges)
        )
        if schema_version is None:
            schema_version = (
                GRAPH_SPEC_SCHEMA_VERSION_R3 if uses_r3 else GRAPH_SPEC_SCHEMA_VERSION
            )
        elif schema_version == GRAPH_SPEC_SCHEMA_VERSION and uses_r3:
            raise ValueError(
                "fan-out edges, reducers and joins require GraphSpec schema version '2'"
            )
        elif schema_version == GRAPH_SPEC_SCHEMA_VERSION_R3 and not uses_r3:
            raise ValueError(
                "GraphSpec schema version '2' requires a fan-out edge, reducer or join"
            )
        payload = canonical_graph_payload(
            schema_version=schema_version,
            max_steps=max_steps,
            nodes=canonical_nodes,
            edges=canonical_edges,
            metadata=frozen_metadata,
            reducers=canonical_reducers,
            joins=canonical_joins,
        )
        return cls(
            schema_version=schema_version,
            graph_id=compute_graph_id(payload),
            max_steps=max_steps,
            nodes=canonical_nodes,
            edges=canonical_edges,
            metadata=frozen_metadata,
            reducers=canonical_reducers,
            joins=canonical_joins,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return canonical_graph_payload(
            schema_version=self.schema_version,
            max_steps=self.max_steps,
            nodes=self.nodes,
            edges=self.edges,
            metadata=self.metadata,
            reducers=self.reducers,
            joins=self.joins,
        )

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload())

    def to_dict(self) -> dict[str, Any]:
        return {"graph_id": self.graph_id, **self.canonical_payload()}

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphSpec":
        """Load a description with exact known fields and verified content identity.

        No callable is imported or invoked. This does not admit an executable
        artifact or restore a scheduler cursor; callers still rebuild trusted
        callables, validate structure and apply the application admission gates.
        """
        _check_record_fields(payload, {
            "schema_version", "graph_id", "max_steps", "nodes", "edges", "metadata",
            "reducers", "joins",
        }, "graph")
        schema_version = str(payload.get("schema_version", ""))
        if schema_version not in SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported GraphSpec schema version: {schema_version!r}"
            )

        raw_nodes = payload.get("nodes", [])
        raw_edges = payload.get("edges", [])
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise TypeError("GraphSpec nodes and edges must be lists")

        for item in raw_nodes:
            _check_record_fields(item, {"name", "implementation_ref", "metadata"}, "node")
        for item in raw_edges:
            _check_record_fields(item, {
                "source", "kind", "target", "router_ref", "declared_targets", "metadata",
            }, "edge")

        nodes = tuple(
            NodeSpec(
                name=str(item["name"]),
                implementation_ref=item.get("implementation_ref"),
                metadata=item.get("metadata", {}),
            )
            for item in raw_nodes
        )
        edges = tuple(
            EdgeSpec(
                source=str(item["source"]),
                kind=item["kind"],
                target=item.get("target"),
                router_ref=item.get("router_ref"),
                declared_targets=tuple(item.get("declared_targets", ())),
                metadata=item.get("metadata", {}),
            )
            for item in raw_edges
        )
        raw_reducers = payload.get("reducers", [])
        raw_joins = payload.get("joins", [])
        if not isinstance(raw_reducers, list) or not isinstance(raw_joins, list):
            raise TypeError("GraphSpec reducers and joins must be lists")
        for item in raw_reducers:
            _check_record_fields(item, {"field", "kind", "ref"}, "reducer")
        for item in raw_joins:
            _check_record_fields(item, {"source", "kind", "quorum", "ref"}, "join")
        reducers = tuple(
            ReducerSpec(
                field=str(item["field"]),
                kind=item["kind"],
                ref=item.get("ref"),
            )
            for item in raw_reducers
        )
        joins = tuple(
            JoinSpec(
                source=str(item["source"]),
                kind=item["kind"],
                quorum=item.get("quorum"),
                ref=item.get("ref"),
            )
            for item in raw_joins
        )
        spec = cls.create(
            schema_version=schema_version,
            max_steps=int(payload["max_steps"]),
            nodes=nodes,
            edges=edges,
            metadata=payload.get("metadata", {}),
            reducers=reducers,
            joins=joins,
        )
        supplied_graph_id = payload.get("graph_id")
        if not isinstance(supplied_graph_id, str):
            raise ValueError("GraphSpec graph_id is required")
        if supplied_graph_id != spec.graph_id:
            raise ValueError(
                "GraphSpec graph_id mismatch: payload content does not match identity"
            )
        return spec

    @classmethod
    def from_json(cls, raw: str) -> "GraphSpec":
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("GraphSpec JSON must contain an object")
        return cls.from_dict(payload)
