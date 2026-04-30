"""GossipBus — SQLite-backed event log replacing volatile .json blobs."""
from __future__ import annotations
import aiosqlite
import json
import time
from typing import Literal, AsyncIterator

EventType = Literal["load", "route", "affinity_check", "dispatch", "error"]

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gossip (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    event_type  TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""


class GossipBus:
    def __init__(self, db_path: str):
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(CREATE_TABLE)
            await db.commit()

    async def emit(self, event_type: EventType, payload: dict) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO gossip (ts, event_type, payload_json) VALUES (?, ?, ?)",
                (time.time(), event_type, json.dumps(payload)),
            )
            await db.commit()

    async def tail(self, *, since: float = 0.0, limit: int = 100) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT ts, event_type, payload_json FROM gossip WHERE ts > ? ORDER BY id DESC LIMIT ?",
                (since, limit),
            )
            rows = await cursor.fetchall()
        return [
            {"ts": r[0], "event_type": r[1], "payload": json.loads(r[2])}
            for r in rows
        ]
