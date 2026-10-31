"""The SQLite database: one file holding settings, presets, cache and history."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    type     TEXT NOT NULL,
    format   TEXT NOT NULL,
    options  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remembered_matches (
    kind       TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    year       INTEGER NOT NULL DEFAULT 0,
    tmdb_id    INTEGER,
    label      TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (kind, norm_title, year)
);
CREATE TABLE IF NOT EXISTS tmdb_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  REAL NOT NULL,
    finished_at REAL,
    source      TEXT NOT NULL,
    preset_name TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    src        TEXT,
    dst        TEXT,
    size       INTEGER,
    mtime_ns   INTEGER,
    state      TEXT NOT NULL,
    error      TEXT,
    undo_state TEXT,
    undo_note  TEXT,
    undone_at  REAL
);
CREATE INDEX IF NOT EXISTS operations_run ON operations(run_id, seq);
"""


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    @classmethod
    async def open(cls, path: Path) -> Database:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        db = cls(conn)
        await db._migrate()
        return db

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        version = int(row[0]) if row else 0
        if version < 1:
            await self.conn.executescript(_SCHEMA_V1)
            await self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            await self.conn.commit()

    async def close(self) -> None:
        await self.conn.close()
