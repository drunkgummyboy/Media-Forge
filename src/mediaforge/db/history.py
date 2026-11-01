"""Runs and their operations: written before each change, settled right after."""

from __future__ import annotations

import time
from collections.abc import Collection, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mediaforge.db.database import Database
from mediaforge.models.history import (
    HISTORY_LIMIT,
    Operation,
    OpKind,
    OpState,
    RunSource,
    RunSummary,
    UndoState,
)
from mediaforge.models.media import MediaKind

_OP_COLUMNS = (
    "id, run_id, seq, kind, src, dst, size, mtime_ns, state, error, "
    "undo_state, undo_note, undone_at"
)

_SUMMARY_SQL = """
SELECT r.id, r.started_at, r.finished_at, r.source, r.preset_name, r.kinds,
  COALESCE(SUM(o.kind = 'rename' AND o.state = 'done'), 0),
  COALESCE(SUM(o.kind = 'create_file' AND o.state = 'done'), 0),
  COALESCE(SUM(o.state = 'done' AND o.kind != 'delete_file'
               AND (o.undo_state IS NULL OR o.undo_state != 'done')), 0),
  COALESCE(SUM(o.undo_state = 'done'), 0)
FROM runs r LEFT JOIN operations o ON o.run_id = r.id
GROUP BY r.id
ORDER BY r.started_at DESC, r.id DESC
"""


def _ts(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value) if value is not None else None


def _operation(row: Iterable[Any]) -> Operation:
    (op_id, run_id, seq, kind, src, dst, size, mtime_ns, state, error, undo_state, note, undone) = (
        row
    )
    return Operation(
        id=op_id,
        run_id=run_id,
        seq=seq,
        kind=OpKind(kind),
        src=src,
        dst=dst,
        size=size,
        mtime_ns=mtime_ns,
        state=OpState(state),
        error=error,
        undo_state=UndoState(undo_state) if undo_state else None,
        undo_note=note,
        undone_at=_ts(undone),
    )


class HistoryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def start_run(
        self,
        source: RunSource,
        preset_name: str | None,
        started_at: datetime | None = None,
        kinds: Collection[MediaKind] = (),
    ) -> int:
        when = started_at.timestamp() if started_at else time.time()
        cur = await self._db.conn.execute(
            "INSERT INTO runs (started_at, source, preset_name, kinds) VALUES (?, ?, ?, ?)",
            (when, source.value, preset_name, ",".join(sorted(k.value for k in set(kinds)))),
        )
        await self._db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def record_pending(
        self, run_id: int, kind: OpKind, src: Path | None, dst: Path | None
    ) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO operations (run_id, seq, kind, src, dst, state) VALUES "
            "(?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM operations WHERE run_id = ?), ?, ?, ?, ?)",
            (
                run_id,
                run_id,
                kind.value,
                str(src) if src else None,
                str(dst) if dst else None,
                OpState.PENDING.value,
            ),
        )
        await self._db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def mark_done(
        self,
        op_id: int,
        size: int | None = None,
        mtime_ns: int | None = None,
        dst: Path | None = None,
    ) -> None:
        await self._db.conn.execute(
            "UPDATE operations SET state = ?, size = COALESCE(?, size), "
            "mtime_ns = COALESCE(?, mtime_ns), dst = COALESCE(?, dst) WHERE id = ?",
            (OpState.DONE.value, size, mtime_ns, str(dst) if dst else None, op_id),
        )
        await self._db.conn.commit()

    async def mark_failed(self, op_id: int, error: str) -> None:
        await self._db.conn.execute(
            "UPDATE operations SET state = ?, error = ? WHERE id = ?",
            (OpState.FAILED.value, error, op_id),
        )
        await self._db.conn.commit()

    async def finish_run(self, run_id: int) -> bool:
        """Close a run. A run that changed nothing is removed; returns whether it was kept.

        Runs with unsettled (pending) changes are kept so crash recovery can settle them.
        """
        conn = self._db.conn
        async with conn.execute(
            "SELECT COUNT(*) FROM operations WHERE run_id = ? AND state IN (?, ?)",
            (run_id, OpState.DONE.value, OpState.PENDING.value),
        ) as cur:
            row = await cur.fetchone()
        kept = bool(row and row[0])
        if kept:
            await conn.execute(
                "UPDATE runs SET finished_at = ? WHERE id = ?", (time.time(), run_id)
            )
        else:
            await conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        await conn.execute(
            "DELETE FROM runs WHERE id NOT IN "
            "(SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT ?)",
            (HISTORY_LIMIT,),
        )
        await conn.commit()
        return kept

    async def list_runs(self) -> list[RunSummary]:
        async with self._db.conn.execute(_SUMMARY_SQL) as cur:
            rows = await cur.fetchall()
        return [
            RunSummary(
                id=r[0],
                started_at=datetime.fromtimestamp(r[1]),
                finished_at=_ts(r[2]),
                source=RunSource(r[3]),
                preset_name=r[4],
                kinds=frozenset(MediaKind(k) for k in r[5].split(",") if k),
                file_count=r[6],
                created_count=r[7],
                undoable_count=r[8],
                undone_count=r[9],
            )
            for r in rows
        ]

    async def was_undone(self, src: Path) -> bool:
        """Whether a rename away from `src` was undone (the file was put back there)."""
        async with self._db.conn.execute(
            "SELECT 1 FROM operations WHERE kind = ? AND src = ? COLLATE NOCASE "
            "AND undo_state = ? LIMIT 1",
            (OpKind.RENAME.value, str(src), UndoState.DONE.value),
        ) as cur:
            return await cur.fetchone() is not None

    async def _select_ops(self, where: str, params: Sequence[Any]) -> list[Operation]:
        async with self._db.conn.execute(
            f"SELECT {_OP_COLUMNS} FROM operations WHERE {where} ORDER BY run_id, seq", params
        ) as cur:
            rows = await cur.fetchall()
        return [_operation(r) for r in rows]

    async def operations(self, run_id: int) -> list[Operation]:
        return await self._select_ops("run_id = ?", (run_id,))

    async def get_operations(self, ids: Sequence[int]) -> list[Operation]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return await self._select_ops(f"id IN ({marks})", list(ids))

    async def pending_operations(self) -> list[Operation]:
        return await self._select_ops("state = ?", (OpState.PENDING.value,))

    async def pending_undos(self) -> list[Operation]:
        return await self._select_ops("undo_state = ?", (UndoState.PENDING.value,))

    async def mark_undo(self, op_id: int, state: UndoState, note: str | None = None) -> None:
        undone_at = time.time() if state is UndoState.DONE else None
        await self._db.conn.execute(
            "UPDATE operations SET undo_state = ?, undo_note = ?, undone_at = ? WHERE id = ?",
            (state.value, note, undone_at, op_id),
        )
        await self._db.conn.commit()
