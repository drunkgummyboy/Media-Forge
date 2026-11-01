import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from mediaforge.core import fsops
from mediaforge.core.executor import Executor, ProgressEvent, RunResult
from mediaforge.core.planner import build_plan
from mediaforge.db.database import Database
from mediaforge.db.history import HistoryRepo
from mediaforge.models.history import OpKind, OpState, RunSource
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.plan import Plan, PlanInput, PlannedTrailer
from mediaforge.models.preset import PresetOptions


@pytest_asyncio.fixture
async def history(tmp_path: Path) -> AsyncIterator[HistoryRepo]:
    db = await Database.open(tmp_path / "h.db")
    try:
        yield HistoryRepo(db)
    finally:
        await db.close()


class FakePosters:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def image(self, path: str, size: str) -> bytes:
        self.calls.append((path, size))
        return b"jpeg-bytes"


class FakeTrailers:
    async def download(self, trailer: PlannedTrailer) -> Path | None:
        url = trailer.target_stem.with_name(trailer.target_stem.name + ".url")
        url.write_text("[InternetShortcut]\nURL=https://example\n", encoding="utf-8")
        return url


def film(title: str, year: int = 2024) -> ResolvedMedia:
    parsed = ParsedMedia(source_name="m.mkv", kind=MediaKind.MOVIE, title=title)
    return ResolvedMedia(
        parsed=parsed,
        title=title,
        year=year,
        episode_title=None,
        match_state=MatchState.CONFIDENT,
        poster_path="/p.jpg",
    )


def make_inputs(root: Path, names: dict[str, str]) -> list[PlanInput]:
    inputs: list[PlanInput] = []
    for i, (filename, title) in enumerate(names.items(), start=1):
        src = root / filename
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"video")
        inputs.append(
            PlanInput(item_id=i, source=src, root=root, dropped_folder=root, media=film(title))
        )
    return inputs


async def run(
    history: HistoryRepo,
    plan: Plan,
    events: list[ProgressEvent] | None = None,
    cancel: asyncio.Event | None = None,
    posters: FakePosters | None = None,
    trailers: FakeTrailers | None = None,
) -> RunResult:
    executor = Executor(history, posters or FakePosters(), trailers)
    sink: list[ProgressEvent] = events if events is not None else []
    return await executor.run(
        plan,
        source=RunSource.APP,
        preset_name="Test",
        on_progress=sink.append,
        cancel=cancel or asyncio.Event(),
    )


async def test_rename_run_journals_everything(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"x.mkv": "Dune"})
    plan = build_plan(inputs, "{ny}/{ny}", PresetOptions(movie_poster=True))
    posters = FakePosters()
    result = await run(history, plan, posters=posters)

    target = tmp_path / "Dune (2024)" / "Dune (2024).mkv"
    assert target.read_bytes() == b"video"
    assert (tmp_path / "Dune (2024)" / "Dune (2024) - Poster.jpg").read_bytes() == b"jpeg-bytes"
    assert posters.calls == [("/p.jpg", "w780")]
    assert (result.renamed, result.posters, result.failed) == (1, 1, 0)
    assert result.run_id is not None

    ops = await history.operations(result.run_id)
    assert [o.kind for o in ops] == [OpKind.CREATE_DIR, OpKind.RENAME, OpKind.CREATE_FILE]
    assert all(o.state is OpState.DONE for o in ops)
    assert ops[0].dst == str(tmp_path / "Dune (2024)")
    assert ops[1].src == str(tmp_path / "x.mkv") and ops[1].dst == str(target)
    assert ops[2].size == len(b"jpeg-bytes") and ops[2].mtime_ns


async def test_order_clean_then_files_then_prune(tmp_path: Path, history: HistoryRepo) -> None:
    (tmp_path / "Thumbs.db").write_bytes(b"junk")
    inputs = make_inputs(tmp_path, {"sub/x.mkv": "Dune"})
    plan = build_plan(inputs, "{ny}", PresetOptions(clean_junk=True, prune_empty=True))
    result = await run(history, plan)

    assert (result.cleaned, result.renamed, result.pruned) == (1, 1, 1)
    assert not (tmp_path / "sub").exists()
    assert result.run_id is not None
    ops = await history.operations(result.run_id)
    assert [o.kind for o in ops] == [OpKind.DELETE_FILE, OpKind.RENAME, OpKind.REMOVE_DIR]
    assert ops[2].src == str(tmp_path / "sub")


async def test_one_failure_does_not_stop_run(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"})
    plan = build_plan(inputs, "{ny}", PresetOptions())
    (tmp_path / "a.mkv").unlink()  # vanished after planning
    result = await run(history, plan)
    assert (result.renamed, result.failed) == (1, 1)
    assert (tmp_path / "Heat (2024).mkv").exists()
    assert result.errors and "a.mkv" in result.errors[0]


async def test_locked_file_is_error_not_crash(
    tmp_path: Path, history: HistoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review focus 3: a file open in a player or being scanned by Plex.
    inputs = make_inputs(tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat", "c.mkv": "Ran"})
    plan = build_plan(inputs, "{ny}", PresetOptions())
    real_move = fsops.move_file

    def locked(src: Path, dst: Path) -> None:
        if src.name == "b.mkv":
            raise PermissionError(32, "The process cannot access the file")
        real_move(src, dst)

    monkeypatch.setattr(fsops, "move_file", locked)
    events: list[ProgressEvent] = []
    result = await run(history, plan, events)
    assert (result.renamed, result.failed) == (2, 1)
    assert result.run_id is not None
    ops = await history.operations(result.run_id)
    failed = [o for o in ops if o.state is OpState.FAILED]
    assert len(failed) == 1 and "cannot access" in (failed[0].error or "")
    assert [e.status for e in events if e.item_id == 2][-1] == "error"


async def test_share_disappears_mid_run(
    tmp_path: Path, history: HistoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review focus 2: the NAS share goes away after the first file.
    inputs = make_inputs(tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat", "c.mkv": "Ran"})
    plan = build_plan(inputs, "{ny}", PresetOptions())
    real_move = fsops.move_file
    calls = [0]

    def flaky(src: Path, dst: Path) -> None:
        calls[0] += 1
        if calls[0] > 1:
            raise OSError(53, "The network path was not found")
        real_move(src, dst)

    monkeypatch.setattr(fsops, "move_file", flaky)
    result = await run(history, plan)
    assert (result.renamed, result.failed) == (1, 2)
    assert await history.pending_operations() == []


async def test_cancel_between_files(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"})
    plan = build_plan(inputs, "{ny}", PresetOptions(movie_poster=True))
    cancel = asyncio.Event()
    events: list[ProgressEvent] = []

    class Stopper(list[ProgressEvent]):
        def append(self, e: ProgressEvent) -> None:
            super().append(e)
            if e.status == "done":
                cancel.set()

    stopper = Stopper()
    executor = Executor(history, FakePosters(), None)
    result = await executor.run(
        plan, source=RunSource.APP, preset_name=None, on_progress=stopper.append, cancel=cancel
    )
    events.extend(stopper)
    assert result.cancelled
    assert result.renamed == 1 and result.posters == 0
    assert (tmp_path / "Alien (2024).mkv").exists()
    assert (tmp_path / "b.mkv").exists()


async def test_poster_not_overwritten(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"x.mkv": "Dune"})
    plan = build_plan(inputs, "{ny}", PresetOptions(movie_poster=True))
    existing = tmp_path / "Dune (2024) - Poster.jpg"
    existing.write_bytes(b"mine")  # appeared after planning
    result = await run(history, plan)
    assert existing.read_bytes() == b"mine"
    assert result.posters == 0
    assert result.run_id is not None
    kinds = [o.kind for o in await history.operations(result.run_id)]
    assert OpKind.CREATE_FILE not in kinds


async def test_poster_skipped_when_its_file_failed(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"})
    plan = build_plan(inputs, "{ny}", PresetOptions(movie_poster=True))
    (tmp_path / "a.mkv").unlink()
    result = await run(history, plan)
    assert result.posters == 1
    assert not (tmp_path / "Alien (2024) - Poster.jpg").exists()
    assert (tmp_path / "Heat (2024) - Poster.jpg").exists()


async def test_trailer_journaled_with_actual_path(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"x.mkv": "Dune"})
    plan = build_plan(inputs, "{ny}", PresetOptions(trailer=True))
    result = await run(history, plan, trailers=FakeTrailers())
    assert result.trailers == 1
    assert result.run_id is not None
    [trailer_op] = [
        o for o in await history.operations(result.run_id) if o.kind is OpKind.CREATE_FILE
    ]
    assert trailer_op.dst == str(tmp_path / "Dune (2024) - Trailer.url")
    assert trailer_op.state is OpState.DONE and trailer_op.size


async def test_junk_deletes_are_journaled_as_delete_file(
    tmp_path: Path, history: HistoryRepo
) -> None:
    (tmp_path / "desktop.ini").write_bytes(b"j")
    inputs = make_inputs(tmp_path, {"x.mkv": "Dune"})
    plan = build_plan(inputs, "{ny}", PresetOptions(clean_junk=True))
    result = await run(history, plan)
    assert result.run_id is not None
    [junk] = [o for o in await history.operations(result.run_id) if o.kind is OpKind.DELETE_FILE]
    assert junk.src == str(tmp_path / "desktop.ini") and not junk.undoable


async def test_empty_run_leaves_no_history(tmp_path: Path, history: HistoryRepo) -> None:
    inputs = make_inputs(tmp_path, {"Dune (2024).mkv": "Dune"})
    plan = build_plan(inputs, "{ny}", PresetOptions())
    result = await run(history, plan)
    assert result.run_id is None and result.skipped == 1
    assert await history.list_runs() == []


async def test_cancelled_task_still_journals_the_rename(
    tmp_path: Path, history: HistoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review I4: closing the window cancels the run task while a rename is in flight. The
    # rename thread finishes anyway, so the journal must say so (undoable), not lose it.
    import time

    inputs = make_inputs(tmp_path, {"a.mkv": "Alien"})
    plan = build_plan(inputs, "{ny}", PresetOptions())
    real_move = fsops.move_file

    def slow_move(src: Path, dst: Path) -> None:
        time.sleep(0.4)
        real_move(src, dst)

    monkeypatch.setattr(fsops, "move_file", slow_move)
    task = asyncio.create_task(run(history, plan))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "Alien (2024).mkv").exists()
    [summary] = await history.list_runs()
    ops = await history.operations(summary.id)
    assert [(o.kind, o.state) for o in ops] == [(OpKind.RENAME, OpState.DONE)]


# --- a download's leftovers (1.3) ---------------------------------------------------------------


def download(tmp_path: Path, extra: tuple[str, ...] = ()) -> tuple[Plan, Path]:
    """One movie in a release folder with junk beside it, planned into a library folder."""
    from mediaforge.models.plan import PlannedLeftovers

    release = tmp_path / "dl" / "Heat.1995.1080p-GRP"
    old = time.time() - 3600  # written with the download, an hour ago
    for name in ("Sample/heat-sample.mkv", "heat.nfo", "RARBG.txt", *extra):
        (release / name).parent.mkdir(parents=True, exist_ok=True)
        (release / name).write_bytes(b"x")
        os.utime(release / name, (old, old))
    [item] = make_inputs(release, {"heat.1995.mkv": "Heat"})
    item = item.model_copy(update={"root": tmp_path / "lib", "dropped_folder": None})
    plan = build_plan([item], "{ny}/{ny}", PresetOptions())
    plan = plan.model_copy(update={"leftovers": (PlannedLeftovers(folder=release, item_ids=(1,)),)})
    return plan, release


async def test_leftovers_go_after_a_successful_move(tmp_path: Path, history: HistoryRepo) -> None:
    plan, release = download(tmp_path)
    result = await run(history, plan)
    assert result.renamed == 1
    assert not release.exists()  # junk removed, then the emptied release folder
    assert (tmp_path / "dl").exists()  # never above the release folder
    ops = await history.operations(result.run_id or 0)
    kinds = [o.kind for o in ops]
    assert kinds.count(OpKind.DELETE_FILE) == 3
    assert OpKind.REMOVE_DIR in kinds
    assert all(o.state is OpState.DONE for o in ops)


async def test_leftovers_stay_when_a_move_failed(tmp_path: Path, history: HistoryRepo) -> None:
    plan, release = download(tmp_path)
    target = plan.files[0].target
    target.parent.mkdir(parents=True)
    target.write_bytes(b"something already here")  # the move fails: the name is taken
    result = await run(history, plan)
    assert result.failed == 1
    assert (release / "heat.nfo").exists() and (release / "heat.1995.mkv").exists()


async def test_leftovers_stay_when_another_video_is_left(
    tmp_path: Path, history: HistoryRepo
) -> None:
    plan, release = download(tmp_path, extra=("heat.extras.mkv",))
    result = await run(history, plan)
    assert result.renamed == 1
    assert (release / "heat.nfo").exists() and (release / "heat.extras.mkv").exists()


async def test_leftovers_stay_while_another_download_is_coming_in(
    tmp_path: Path, history: HistoryRepo
) -> None:
    plan, release = download(tmp_path, extra=("heat.part2.mkv.!qB",))
    result = await run(history, plan)
    assert result.renamed == 1
    assert (release / "heat.nfo").exists()
