import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.plugins.interrupt_guard import resume_policy, ResumeMode


def test_merge_mode_blends_resume_value_into_scratchpad():
    state = PerpetuaState(session_id="t1", scratchpad={"existing": "old"})
    result = resume_policy(state, {"existing": "new", "added": "x"}, mode=ResumeMode.MERGE)
    assert result.scratchpad == {"existing": "new", "added": "x"}


def test_drop_mode_replaces_one_key_only():
    state = PerpetuaState(session_id="t1", scratchpad={"a": 1, "b": 2})
    result = resume_policy(state, {"b": 99}, mode=ResumeMode.DROP, drop_key="b")
    assert result.scratchpad == {"a": 1, "b": 99}


def test_drop_mode_requires_drop_key():
    state = PerpetuaState(session_id="t1")
    with pytest.raises(ValueError):
        resume_policy(state, {"x": 1}, mode=ResumeMode.DROP)
