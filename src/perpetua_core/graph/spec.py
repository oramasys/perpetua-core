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
SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS = frozenset({GRAPH_SPEC_SCHEMA_VERSION})
EdgeKind = Literal["static", "conditional"]
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
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "max_steps": max_steps,
        "nodes": [node.to_dict() for node in sorted(nodes, key=_node_sort_key)],
        "edges": [edge.to_dict() for edge in sorted(edges, key=_edge_sort_key)],
        "metadata": _thaw_json(metadata),
    }


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_graph_id(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes, key=_node_sort_key)))
        object.__setattr__(self, "edges", tuple(sorted(self.edges, key=_edge_sort_key)))
        object.__setattr__(self, "metadata", _freeze_json(dict(self.metadata)))

    @classmethod
    def create(
        cls,
        *,
        max_steps: int,
        nodes: tuple[NodeSpec, ...] = (),
        edges: tuple[EdgeSpec, ...] = (),
        metadata: Mapping[str, Any] | None = None,
        schema_version: str = GRAPH_SPEC_SCHEMA_VERSION,
    ) -> "GraphSpec":
        frozen_metadata = _freeze_json(dict(metadata or {}))
        if not isinstance(frozen_metadata, Mapping):
            raise TypeError("GraphSpec metadata must be a mapping")
        canonical_nodes = tuple(sorted(nodes, key=_node_sort_key))
        canonical_edges = tuple(sorted(edges, key=_edge_sort_key))
        payload = canonical_graph_payload(
            schema_version=schema_version,
            max_steps=max_steps,
            nodes=canonical_nodes,
            edges=canonical_edges,
            metadata=frozen_metadata,
        )
        return cls(
            schema_version=schema_version,
            graph_id=compute_graph_id(payload),
            max_steps=max_steps,
            nodes=canonical_nodes,
            edges=canonical_edges,
            metadata=frozen_metadata,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return canonical_graph_payload(
            schema_version=self.schema_version,
            max_steps=self.max_steps,
            nodes=self.nodes,
            edges=self.edges,
            metadata=self.metadata,
        )

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload())

    def to_dict(self) -> dict[str, Any]:
        return {"graph_id": self.graph_id, **self.canonical_payload()}

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphSpec":
        schema_version = str(payload.get("schema_version", ""))
        if schema_version not in SUPPORTED_GRAPH_SPEC_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported GraphSpec schema version: {schema_version!r}"
            )

        raw_nodes = payload.get("nodes", [])
        raw_edges = payload.get("edges", [])
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise TypeError("GraphSpec nodes and edges must be lists")

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
        spec = cls.create(
            schema_version=schema_version,
            max_steps=int(payload["max_steps"]),
            nodes=nodes,
            edges=edges,
            metadata=payload.get("metadata", {}),
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
