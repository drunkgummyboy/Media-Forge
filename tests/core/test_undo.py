import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from mediaforge.core.executor import Executor, RunResult
from mediaforge.core.planner import build_plan
from mediaforge.core.undo import UndoService
from mediaforge.db.database import Database
from mediaforge.db.history import HistoryRepo
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.plan import PlanInput
from mediaforge.models.preset import PresetOptions


@pytest_asyncio.fixture
async def history(tmp_path: Path) -> AsyncIterator[HistoryRepo]:
    db = await Database.open(tmp_path / "h.db")
    try:
        yield HistoryRepo(db)
    finally:
        await db.close()


class FakePosters:
    async def image(self, path: str, size: str) -> bytes:
        return b"poster"


def film(title: str) -> ResolvedMedia:
    parsed = ParsedMedia(source_name="m.mkv", kind=MediaKind.MOVIE, title=title)
    return ResolvedMedia(
        parsed=parsed,
        title=title,
        year=2024,
        episode_title=None,
        match_state=MatchState.CONFIDENT,
        poster_path="/p.jpg",
    )


async def forge(
    history: HistoryRepo, root: Path, names: dict[str, str], fmt: str, opts: PresetOptions
) -> RunResult:
    inputs: list[PlanInput] = []
    for i, (filename, title) in enumerate(names.items(), start=1):
        src = root / filename
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"video")
        inputs.append(
            PlanInput(item_id=i, source=src, root=root, dropped_folder=root, media=film(title))
        )
    plan = build_plan(inputs, fmt, opts)
    result = await Executor(history, FakePosters(), None).run(
        plan,
        source=RunSource.APP,
        preset_name=None,
        on_progress=lambda e: None,
        cancel=asyncio.Event(),
    )
    assert result.run_id is not None
    return result


async def test_undo_whole_run_restores_names_and_removes_created_dirs_and_posters(
    tmp_path: Path, history: HistoryRepo
) -> None:
    run = await forge(
        history, tmp_path, {"x.mkv": "Dune"}, "{ny}/{ny}", PresetOptions(movie_poster=True)
    )
    assert run.run_id is not None
    assert (tmp_path / "Dune (2024)" / "Dune (2024) - Poster.jpg").exists()

    result = await UndoService(history).undo_run(run.run_id)
    assert result.undone == 3 and result.skipped == []
    assert (tmp_path / "x.mkv").read_bytes() == b"video"
    assert not (tmp_path / "Dune (2024)").exists()
    [summary] = await history.list_runs()
    assert summary.status == "undone"


async def test_undo_selected_files_only(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(
        history, tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"}, "{ny}", PresetOptions()
    )
    assert run.run_id is not None
    ops = await history.operations(run.run_id)
    first = next(o for o in ops if o.kind is OpKind.RENAME and o.src == str(tmp_path / "a.mkv"))

    result = await UndoService(history).undo_operations([first.id])
    assert result.undone == 1
    assert (tmp_path / "a.mkv").exists()
    assert (tmp_path / "Heat (2024).mkv").exists()
    [summary] = await history.list_runs()
    assert summary.status == "partly-undone"


async def test_undo_older_run_out_of_order(tmp_path: Path, history: HistoryRepo) -> None:
    first = await forge(history, tmp_path, {"a.mkv": "Alien"}, "{ny}", PresetOptions())
    await forge(history, tmp_path, {"b.mkv": "Heat"}, "{ny}", PresetOptions())
    assert first.run_id is not None
    result = await UndoService(history).undo_run(first.run_id)
    assert result.undone == 1
    assert (tmp_path / "a.mkv").exists()
    assert (tmp_path / "Heat (2024).mkv").exists()


async def test_skip_when_moved_again(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(history, tmp_path, {"a.mkv": "Alien"}, "{ny}", PresetOptions())
    assert run.run_id is not None
    (tmp_path / "Alien (2024).mkv").rename(tmp_path / "elsewhere.mkv")
    result = await UndoService(history).undo_run(run.run_id)
    assert result.undone == 0
    assert result.skipped == [
        (str(tmp_path / "Alien (2024).mkv"), "file was moved or renamed again")
    ]


async def test_skip_when_old_name_taken(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(history, tmp_path, {"a.mkv": "Alien"}, "{ny}", PresetOptions())
    assert run.run_id is not None
    (tmp_path / "a.mkv").write_bytes(b"new download")
    result = await UndoService(history).undo_run(run.run_id)
    assert result.skipped == [
        (str(tmp_path / "Alien (2024).mkv"), "something else now has the old name")
    ]
    assert (tmp_path / "a.mkv").read_bytes() == b"new download"


async def test_poster_changed_since_is_kept(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(
        history, tmp_path, {"x.mkv": "Dune"}, "{ny}", PresetOptions(movie_poster=True)
    )
    assert run.run_id is not None
    poster = tmp_path / "Dune (2024) - Poster.jpg"
    poster.write_bytes(b"a different, edited poster")
    result = await UndoService(history).undo_run(run.run_id)
    assert (str(poster), "file has changed since") in result.skipped
    assert poster.read_bytes() == b"a different, edited poster"
    assert (tmp_path / "x.mkv").exists()


async def test_pruned_folder_recreated(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(
        history, tmp_path, {"sub/x.mkv": "Dune"}, "{ny}", PresetOptions(prune_empty=True)
    )
    assert run.run_id is not None
    assert not (tmp_path / "sub").exists()
    result = await UndoService(history).undo_run(run.run_id)
    assert result.undone == 2
    assert (tmp_path / "sub" / "x.mkv").exists()


async def test_undo_is_idempotent(tmp_path: Path, history: HistoryRepo) -> None:
    run = await forge(history, tmp_path, {"a.mkv": "Alien"}, "{ny}", PresetOptions())
    assert run.run_id is not None
    service = UndoService(history)
    assert (await service.undo_run(run.run_id)).undone == 1
    again = await service.undo_run(run.run_id)
    assert again.undone == 0 and again.skipped == []


async def test_create_dir_removed_last_and_only_if_empty(
    tmp_path: Path, history: HistoryRepo
) -> None:
    run = await forge(history, tmp_path, {"x.mkv": "Dune"}, "{ny}/{ny}", PresetOptions())
    assert run.run_id is not None
    (tmp_path / "Dune (2024)" / "my notes.txt").write_text("keep me", encoding="utf-8")
    result = await UndoService(history).undo_run(run.run_id)
    assert (tmp_path / "x.mkv").exists()
    assert (str(tmp_path / "Dune (2024)"), "folder isn't empty") in result.skipped
    assert (tmp_path / "Dune (2024)" / "my notes.txt").exists()


async def test_junk_delete_reported_once(tmp_path: Path, history: HistoryRepo) -> None:
    (tmp_path / "Thumbs.db").write_bytes(b"j")
    run = await forge(history, tmp_path, {"a.mkv": "Alien"}, "{ny}", PresetOptions(clean_junk=True))
    assert run.run_id is not None
    service = UndoService(history)
    first = await service.undo_run(run.run_id)
    assert (str(tmp_path / "Thumbs.db"), "deleted junk can't be restored") in first.skipped
    assert (await service.undo_run(run.run_id)).skipped == []


async def test_two_undos_at_once_undo_once(tmp_path: Path, history: HistoryRepo) -> None:
    # Review I1: double-clicking Undo must not reverse twice or rewrite DONE as SKIPPED.
    run = await forge(
        history, tmp_path, {f"m{n}.mkv": f"Movie {n}" for n in range(6)}, "{ny}", PresetOptions()
    )
    assert run.run_id is not None
    service = UndoService(history)
    first, second = await asyncio.gather(service.undo_run(run.run_id), service.undo_run(run.run_id))
    assert first.undone + second.undone == 6
    assert first.skipped == [] and second.skipped == []
    ops = await history.operations(run.run_id)
    assert all(o.undo_state is not None and o.undo_state.value == "done" for o in ops)
