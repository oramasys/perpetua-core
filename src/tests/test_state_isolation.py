"""Regression tests for PerpetuaState generation isolation."""
from perpetua_core.state import PerpetuaState


def test_merge_deep_isolates_untouched_mutable_fields() -> None:
    original = PerpetuaState(
        session_id="isolation",
        messages=[{"role": "user", "content": "hello"}],
        scratchpad={"nested": {"count": 1}},
        nodes_visited=["a"],
        metadata={"nested": {"flag": True}},
    )

    merged = original.merge({})

    merged.messages[0]["content"] = "changed"
    merged.scratchpad["nested"]["count"] = 2
    merged.nodes_visited.append("b")
    merged.metadata["nested"]["flag"] = False

    assert original.messages == [{"role": "user", "content": "hello"}]
    assert original.scratchpad == {"nested": {"count": 1}}
    assert original.nodes_visited == ["a"]
    assert original.metadata == {"nested": {"flag": True}}


def test_merge_still_applies_delta() -> None:
    original = PerpetuaState(session_id="delta", retry_count=1)
    merged = original.merge({"retry_count": 2, "status": "running"})

    assert merged.retry_count == 2
    assert merged.status == "running"
    assert original.retry_count == 1
    assert original.status == "idle"
