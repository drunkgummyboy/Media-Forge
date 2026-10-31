"""A small key/value cache for TMDB responses."""

from __future__ import annotations

import json
import time
from typing import Any

from mediaforge.db.database import Database


class CacheRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_json(self, key: str, max_age_s: float) -> Any | None:
        async with self._db.conn.execute(
            "SELECT payload, fetched_at FROM tmdb_cache WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or time.time() - float(row[1]) > max_age_s:
            return None
        return json.loads(row[0])

    async def put_json(self, key: str, value: Any) -> None:
        await self._db.conn.execute(
            "INSERT INTO tmdb_cache (key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
            "fetched_at = excluded.fetched_at",
            (key, json.dumps(value), time.time()),
        )
        await self._db.conn.commit()

    async def purge(self, older_than_s: float) -> int:
        cur = await self._db.conn.execute(
            "DELETE FROM tmdb_cache WHERE fetched_at < ?", (time.time() - older_than_s,)
        )
        await self._db.conn.commit()
        return cur.rowcount
