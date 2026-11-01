from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from mediaforge.core.recovery import recover
from mediaforge.db.database import Database
from mediaforge.db.history import HistoryRepo
from mediaforge.models.history import OpKind, OpState, RunSource, UndoState


@pytest_asyncio.fixture
async def history(tmp_path: Path) -> AsyncIterator[HistoryRepo]:
    db = await Database.open(tmp_path / "h.db")
    try:
        yield HistoryRepo(db)
    finally:
        await db.close()


async def test_recover_pending_rename_done_and_failed(tmp_path: Path, history: HistoryRepo) -> None:
    run_id = await history.start_run(RunSource.APP, None)
    moved_dst = tmp_path / "new.mkv"
    moved_dst.write_bytes(b"v")
    done = await history.record_pending(run_id, OpKind.RENAME, tmp_path / "old.mkv", moved_dst)
    stuck_src = tmp_path / "still.mkv"
    stuck_src.write_bytes(b"v")
    failed = await history.record_pending(run_id, OpKind.RENAME, stuck_src, tmp_path / "x.mkv")
    poster = tmp_path / "p.jpg"
    poster.write_bytes(b"jpg")
    created = await history.record_pending(run_id, OpKind.CREATE_FILE, None, poster)

    assert await recover(history) == 3
    ops = {o.id: o for o in await history.operations(run_id)}
    assert ops[done].state is OpState.DONE
    assert ops[failed].state is OpState.FAILED and ops[failed].error == "interrupted"
    assert ops[created].state is OpState.DONE and ops[created].size == 3
    assert await history.pending_operations() == []


async def test_recover_pending_undo(tmp_path: Path, history: HistoryRepo) -> None:
    run_id = await history.start_run(RunSource.APP, None)
    back = tmp_path / "a.mkv"
    back.write_bytes(b"v")
    finished = await history.record_pending(run_id, OpKind.RENAME, back, tmp_path / "b.mkv")
    unclear_src = tmp_path / "c.mkv"
    unclear = await history.record_pending(run_id, OpKind.RENAME, unclear_src, tmp_path / "d.mkv")
    (tmp_path / "d.mkv").write_bytes(b"v")
    for op in (finished, unclear):
        await history.mark_done(op)
        await history.mark_undo(op, UndoState.PENDING)

    assert await recover(history) == 2
    ops = {o.id: o for o in await history.operations(run_id)}
    assert ops[finished].undo_state is UndoState.DONE
    assert ops[unclear].undo_state is UndoState.SKIPPED
    assert ops[unclear].undo_note == "interrupted"


async def test_unreachable_share_leaves_ops_pending(tmp_path: Path, history: HistoryRepo) -> None:
    # Review I5: with the NAS offline both names look missing; don't decide anything yet.
    share = tmp_path / "offline-share"  # never created
    run_id = await history.start_run(RunSource.APP, None)
    rename = await history.record_pending(run_id, OpKind.RENAME, share / "a.mkv", share / "b.mkv")
    poster = await history.record_pending(run_id, OpKind.CREATE_FILE, None, share / "p.jpg")
    assert await recover(history) == 0
    assert {o.id for o in await history.pending_operations()} == {rename, poster}


async def test_case_only_rename_judged_by_real_name(tmp_path: Path, history: HistoryRepo) -> None:
    run_id = await history.start_run(RunSource.APP, None)
    (tmp_path / "The Office.mkv").write_bytes(b"v")
    done = await history.record_pending(
        run_id, OpKind.RENAME, tmp_path / "the office.mkv", tmp_path / "The Office.mkv"
    )
    (tmp_path / "heat.mkv").write_bytes(b"v")
    not_done = await history.record_pending(
        run_id, OpKind.RENAME, tmp_path / "heat.mkv", tmp_path / "Heat.mkv"
    )
    await recover(history)
    ops = {o.id: o for o in await history.operations(run_id)}
    assert ops[done].state is OpState.DONE
    assert ops[not_done].state is OpState.FAILED


async def test_recovery_clears_a_partial_copy(tmp_path: Path) -> None:
    from mediaforge.core import fsops
    from mediaforge.core.recovery import recover
    from mediaforge.db.database import Database
    from mediaforge.db.history import HistoryRepo
    from mediaforge.models.history import OpKind, OpState, RunSource

    db = await Database.open(tmp_path / "h.db")
    try:
        history = HistoryRepo(db)
        src, dst = tmp_path / "dl" / "a.mkv", tmp_path / "lib" / "a.mkv"
        src.parent.mkdir()
        dst.parent.mkdir()
        src.write_bytes(b"video")
        partial = dst.with_name(dst.name + fsops.PARTIAL_SUFFIX)
        partial.write_bytes(b"vid")  # the PC went off during the copy
        run_id = await history.start_run(RunSource.AUTOMATIC, "Movie (Folder)")
        await history.record_pending(run_id, OpKind.RENAME, src, dst)
        await recover(history)
        assert not partial.exists() and src.exists() and not dst.exists()
        [op] = await history.operations(run_id)
        assert op.state is OpState.FAILED
    finally:
        await db.close()
