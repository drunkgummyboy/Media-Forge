"""Match choices the user made, so the same show isn't asked about twice."""

from __future__ import annotations

import time

from pydantic import BaseModel

from mediaforge.db.database import Database
from mediaforge.models.media import MediaKind


class RememberedMatch(BaseModel):
    kind: MediaKind
    norm_title: str
    year: int | None
    tmdb_id: int | None  # None means "don't use TMDB for this title"
    label: str


def _year_key(year: int | None) -> int:
    return year or 0


class MatchRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(
        self, kind: MediaKind, norm_title: str, year: int | None
    ) -> RememberedMatch | None:
        async with self._db.conn.execute(
            "SELECT tmdb_id, label FROM remembered_matches "
            "WHERE kind = ? AND norm_title = ? AND year = ?",
            (kind.value, norm_title, _year_key(year)),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return RememberedMatch(
            kind=kind, norm_title=norm_title, year=year, tmdb_id=row[0], label=row[1]
        )

    async def remember(self, match: RememberedMatch) -> None:
        await self._db.conn.execute(
            "INSERT INTO remembered_matches (kind, norm_title, year, tmdb_id, label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, norm_title, year) DO UPDATE SET "
            "tmdb_id = excluded.tmdb_id, label = excluded.label, created_at = excluded.created_at",
            (
                match.kind.value,
                match.norm_title,
                _year_key(match.year),
                match.tmdb_id,
                match.label,
                time.time(),
            ),
        )
        await self._db.conn.commit()

    async def list(self) -> list[RememberedMatch]:
        async with self._db.conn.execute(
            "SELECT kind, norm_title, year, tmdb_id, label FROM remembered_matches "
            "ORDER BY norm_title, year"
        ) as cur:
            rows = await cur.fetchall()
        return [
            RememberedMatch(
                kind=MediaKind(kind), norm_title=title, year=year or None, tmdb_id=tid, label=label
            )
            for kind, title, year, tid, label in rows
        ]

    async def forget(self, kind: MediaKind, norm_title: str, year: int | None) -> None:
        await self._db.conn.execute(
            "DELETE FROM remembered_matches WHERE kind = ? AND norm_title = ? AND year = ?",
            (kind.value, norm_title, _year_key(year)),
        )
        await self._db.conn.commit()

    async def clear(self) -> None:
        await self._db.conn.execute("DELETE FROM remembered_matches")
        await self._db.conn.commit()
