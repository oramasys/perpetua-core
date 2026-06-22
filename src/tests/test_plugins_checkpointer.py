"""TDD: checkpointer plugin — save/load round-trip."""
import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.plugins.checkpointer import SqliteCheckpointer


@pytest.fixture
async def ckpt(tmp_path):
    c = SqliteCheckpointer(str(tmp_path / "test.db"))
    await c.init_db()
    return c


async def test_save_and_load_roundtrip(ckpt):
    s = PerpetuaState(session_id="sess1", status="running", retry_count=2)
    await ckpt.save(s, node="node_x")
    loaded = await ckpt.load_latest("sess1")
    assert loaded is not None
    assert loaded.session_id == "sess1"
    assert loaded.status == "running"
    assert loaded.retry_count == 2


async def test_load_returns_none_for_unknown_session(ckpt):
    result = await ckpt.load_latest("nonexistent")
    assert result is None


async def test_load_returns_most_recent(ckpt):
    s1 = PerpetuaState(session_id="s", retry_count=1)
    s2 = PerpetuaState(session_id="s", retry_count=2)
    await ckpt.save(s1, node="a")
    await ckpt.save(s2, node="b")
    loaded = await ckpt.load_latest("s")
    assert loaded.retry_count == 2
