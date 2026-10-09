"""SQLite state-snapshot storage; no durable scheduler resume or effect replay.

The legacy table stores session, node and state only. It has no graph identity,
frontier, effect key, approval reservation or provenance. Loading a snapshot and
calling ``ainvoke`` starts at the entry node again; it must not be used to replay
external effects. R4 requires a separately reviewed durable recovery contract.
"""
from __future__ import annotations

import aiosqlite

from perpetua_core.graph.engine import GraphObservation
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
    """Store snapshots for inspection; never infer an authorized replay cursor."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(CREATE_TABLE)
            await db.commit()

    async def save(self, state: PerpetuaState, *, node: str) -> None:
        """Append a state snapshot; ``node`` is evidence, not a restart position."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO checkpoints (session_id, node, state_json) VALUES (?, ?, ?)",
                (state.session_id, node, state.model_dump_json()),
            )
            await db.commit()

    async def on_observation(self, observation: GraphObservation) -> None:
        """Checkpoint each successfully completed node transition.

        ``run_with_plugins`` may dispatch this alongside tracers, audit hooks,
        and other observers during the same graph run. The checkpointer does not
        schedule or traverse the graph itself.

        R3 branch ``node.end`` events contain the same atomically committed
        region state. These rows do not record which branches contributed it;
        audit listeners must consume ``superstep.commit.provenance`` separately.
        """
        event = observation.event
        if event.kind == "node.end" and event.node is not None:
            await self.save(observation.state, node=event.node)

    async def load_latest(self, session_id: str) -> PerpetuaState | None:
        """Read the newest state only; do not resume traversal or approve effects."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT state_json FROM checkpoints WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
            row = await cursor.fetchone()
        return PerpetuaState.model_validate_json(row[0]) if row else None
