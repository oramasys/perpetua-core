"""SQLite checkpointer plugin — persist and resume graph state."""
from __future__ import annotations
import aiosqlite
from perpetua_core.state import PerpetuaState

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    node TEXT NOT NULL,
    state_json TEXT NOT NULL
)
"""


class SqliteCheckpointer:
    def __init__(self, db_path: str):
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(CREATE_TABLE)
            await db.commit()

    async def save(self, state: PerpetuaState, *, node: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO checkpoints (session_id, node, state_json) VALUES (?, ?, ?)",
                (state.session_id, node, state.model_dump_json()),
            )
            await db.commit()

    async def load_latest(self, session_id: str) -> PerpetuaState | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT state_json FROM checkpoints WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
            row = await cursor.fetchone()
        return PerpetuaState.model_validate_json(row[0]) if row else None
