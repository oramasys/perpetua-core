"""TDD: PerpetuaState — write failing tests first."""
import pytest
from perpetua_core.state import PerpetuaState, HardwareTier, TaskType


def test_default_state_fields():
    s = PerpetuaState(session_id="s1")
    assert s.session_id == "s1"
    assert s.messages == []
    assert s.scratchpad == {}
    assert s.status == "idle"
    assert s.error is None
    assert s.nodes_visited == []
    assert s.metadata == {}
    assert s.retry_count == 0
    assert s.target_tier == "shared"
    assert s.task_type == "reasoning"
    assert s.optimize_for == "quality"
    assert s.model_hint is None


def test_merge_applies_delta():
    s = PerpetuaState(session_id="s1")
    s2 = s.merge({"status": "running", "retry_count": 1})
    assert s2.status == "running"
    assert s2.retry_count == 1
    assert s2.session_id == "s1"  # unchanged fields preserved


def test_merge_does_not_mutate_original():
    s = PerpetuaState(session_id="s1")
    _ = s.merge({"status": "done"})
    assert s.status == "idle"


def test_nodes_visited_appended_via_merge():
    s = PerpetuaState(session_id="s1")
    s2 = s.merge({"nodes_visited": [*s.nodes_visited, "router"]})
    assert s2.nodes_visited == ["router"]


def test_pydantic_roundtrip():
    s = PerpetuaState(session_id="abc", task_type="coding", optimize_for="speed")
    json_str = s.model_dump_json()
    s2 = PerpetuaState.model_validate_json(json_str)
    assert s2.session_id == "abc"
    assert s2.task_type == "coding"
    assert s2.optimize_for == "speed"


def test_invalid_status_rejected():
    with pytest.raises(Exception):
        PerpetuaState(session_id="s1", status="invalid_status")


def test_invalid_task_type_rejected():
    with pytest.raises(Exception):
        PerpetuaState(session_id="s1", task_type="dancing")
