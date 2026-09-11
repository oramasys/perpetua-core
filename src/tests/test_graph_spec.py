from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from perpetua_core.graph.spec import EdgeSpec, GraphSpec, NodeSpec


def _basic_spec() -> GraphSpec:
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


def test_node_spec_is_immutable() -> None:
    node = NodeSpec(name="route")
    with pytest.raises(FrozenInstanceError):
        node.name = "other"  # type: ignore[misc]


def test_static_edge_requires_target() -> None:
    with pytest.raises(ValueError, match="static edge requires target"):
        EdgeSpec(source="route", kind="static")


def test_conditional_edge_rejects_static_target() -> None:
    with pytest.raises(ValueError, match="conditional edge must not set target"):
        EdgeSpec(source="route", kind="conditional", target="respond")


def test_metadata_is_deeply_frozen() -> None:
    node = NodeSpec(name="a", metadata={"labels": ["x", "y"]})

    assert node.metadata["labels"] == ("x", "y")
    with pytest.raises(TypeError):
        node.metadata["new"] = "value"  # type: ignore[index]


def test_graph_id_is_stable_across_input_order() -> None:
    a = GraphSpec.create(
        max_steps=20,
        nodes=(
            NodeSpec("b", implementation_ref="tests:b"),
            NodeSpec("a", implementation_ref="tests:a"),
        ),
        edges=(
            EdgeSpec("__start__", "static", target="a"),
            EdgeSpec("a", "static", target="b"),
            EdgeSpec("b", "static", target="__end__"),
        ),
        metadata={"z": 1, "a": 2},
    )
    b = GraphSpec.create(
        max_steps=20,
        nodes=(
            NodeSpec("a", implementation_ref="tests:a"),
            NodeSpec("b", implementation_ref="tests:b"),
        ),
        edges=(
            EdgeSpec("b", "static", target="__end__"),
            EdgeSpec("a", "static", target="b"),
            EdgeSpec("__start__", "static", target="a"),
        ),
        metadata={"a": 2, "z": 1},
    )

    assert a.graph_id == b.graph_id
    assert a.to_dict() == b.to_dict()


def test_graph_spec_round_trips_json() -> None:
    spec = _basic_spec()

    restored = GraphSpec.from_json(spec.to_json())

    assert restored == spec
    assert restored.graph_id == spec.graph_id


def test_unknown_schema_version_fails_closed() -> None:
    payload = _basic_spec().to_dict()
    payload["schema_version"] = "999"

    with pytest.raises(ValueError, match="unsupported GraphSpec schema"):
        GraphSpec.from_dict(payload)


def test_graph_id_tampering_is_rejected() -> None:
    payload = _basic_spec().to_dict()
    payload["nodes"][0]["name"] = "mutated"

    with pytest.raises(ValueError, match="graph_id mismatch"):
        GraphSpec.from_dict(payload)


def test_graph_spec_rejects_missing_identity_and_nonfinite_metadata() -> None:
    payload = _basic_spec().to_dict()
    payload.pop("graph_id")
    with pytest.raises(ValueError, match="graph_id is required"):
        GraphSpec.from_dict(payload)
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(TypeError, match="floats must be finite"):
            GraphSpec.create(max_steps=1, metadata={"value": value})


def test_non_json_metadata_is_rejected() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        NodeSpec("a", metadata={"bad": object()})
