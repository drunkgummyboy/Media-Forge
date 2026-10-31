"""Put things back: any run, or any chosen operations, in any order, with safety checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from mediaforge.core import fsops
from mediaforge.db.history import HistoryRepo
from mediaforge.models.history import Operation, OpKind, OpState, UndoState

log = logging.getLogger("mediaforge.undo")

MOVED_AGAIN = "file was moved or renamed again"
OLD_NAME_TAKEN = "something else now has the old name"
CHANGED_SINCE = "file has changed since"
ALREADY_GONE = "already gone"
FOLDER_NOT_EMPTY = "folder isn't empty"
JUNK_IS_GONE = "deleted junk can't be restored"


class UndoResult(BaseModel):
    undone: int = 0
    skipped: list[tuple[str, str]] = []  # (path, reason)


def _wants_undo(op: Operation) -> bool:
    if op.kind is OpKind.DELETE_FILE:  # reported once, then left alone
        return op.state is OpState.DONE and op.undo_state is None
    return op.undoable


def _order(ops: Sequence[Operation]) -> list[Operation]:
    """Newest first; folders we created go last, deepest first, once they've been emptied."""
    dirs = [o for o in ops if o.kind is OpKind.CREATE_DIR]
    others = [o for o in ops if o.kind is not OpKind.CREATE_DIR]
    others.sort(key=lambda o: (o.run_id, o.seq), reverse=True)
    dirs.sort(key=lambda o: len(Path(o.dst or "").parts), reverse=True)
    return others + dirs


def _reverse(op: Operation) -> tuple[UndoState, str | None]:
    """Undo one operation on disk. Returns the outcome and, when not plainly done, why."""
    src = Path(op.src) if op.src else None
    dst = Path(op.dst) if op.dst else None
    match op.kind:
        case OpKind.RENAME:
            assert src is not None and dst is not None
            if not fsops.exists(dst):
                return UndoState.SKIPPED, MOVED_AGAIN
            same_file = str(src).casefold() == str(dst).casefold()
            if fsops.exists(src) and not same_file:
                return UndoState.SKIPPED, OLD_NAME_TAKEN
            fsops.make_dirs(src.parent)
            fsops.move_file(dst, src)
            return UndoState.DONE, None
        case OpKind.CREATE_FILE:
            assert dst is not None
            if not fsops.exists(dst):
                return UndoState.DONE, ALREADY_GONE
            if op.size is None or fsops.stamp(dst) != (op.size, op.mtime_ns):
                return UndoState.SKIPPED, CHANGED_SINCE
            fsops.remove_file(dst)
            return UndoState.DONE, None
        case OpKind.CREATE_DIR:
            assert dst is not None
            if not fsops.is_dir(dst):
                return UndoState.DONE, None
            if not fsops.remove_empty_dir(dst):
                return UndoState.SKIPPED, FOLDER_NOT_EMPTY
            return UndoState.DONE, None
        case OpKind.REMOVE_DIR:
            assert src is not None
            fsops.make_dirs(src)
            return UndoState.DONE, None
        case OpKind.DELETE_FILE:
            return UndoState.SKIPPED, JUNK_IS_GONE


class UndoService:
    def __init__(self, history: HistoryRepo) -> None:
        self._history = history
        self._lock = asyncio.Lock()  # one undo at a time; ops are re-read inside it

    async def undo_run(self, run_id: int) -> UndoResult:
        async with self._lock:
            return await self._undo(await self._history.operations(run_id))

    async def undo_operations(self, op_ids: Sequence[int]) -> UndoResult:
        async with self._lock:
            return await self._undo(await self._history.get_operations(op_ids))

    async def _undo(self, ops: Sequence[Operation]) -> UndoResult:
        result = UndoResult()
        for op in _order([o for o in ops if _wants_undo(o)]):
            path = op.dst or op.src or ""
            await self._history.mark_undo(op.id, UndoState.PENDING)
            try:
                state, note = await asyncio.to_thread(_reverse, op)
            except OSError as exc:
                state, note = UndoState.SKIPPED, str(exc)
            await self._history.mark_undo(op.id, state, note)
            if state is UndoState.DONE:
                result.undone += 1
                if op.kind is OpKind.RENAME:
                    log.info("Put back: %s", op.src, extra={"success": True})
            if note:
                result.skipped.append((path, note))
                log.warning("Not undone: %s (%s)", path, note)
        return result
