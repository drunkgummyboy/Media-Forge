import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
import pytest_asyncio

from mediaforge.app import Services
from mediaforge.core.matcher import Resolution
from mediaforge.core.queue import ItemStatus
from mediaforge.models.media import MatchState, ResolvedMedia
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.queue_view import QueueView
from mediaforge.ui.theme import DARK

from helpers.tk import pump, pump_until


@pytest_asyncio.fixture
async def view(tk_root: DnDCTk, services: Services) -> AsyncIterator[QueueView]:
    v = QueueView(tk_root, services, DARK)
    v.pack(fill="both", expand=True)
    await pump(tk_root, 0.05)
    try:
        yield v
    finally:
        v.destroy()


def make_files(folder: Path, names: Sequence[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"video")


async def add_and_settle(tk_root: DnDCTk, view: QueueView, folder: Path) -> None:
    await view.services.queue.add_paths([folder], recursive=True)
    await view.services.queue.wait_idle()
    await pump(tk_root, 0.25)


async def test_empty_state_shown_first(view: QueueView) -> None:
    assert view.empty_state.winfo_ismapped()
    assert not view.list.winfo_ismapped()


async def test_queue_view_lists_added_files(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    names = [f"Show.Name.S01E0{i}.mkv" for i in range(1, 6)]
    make_files(tmp_path / "in", names)
    await add_and_settle(tk_root, view, tmp_path / "in")
    rows = view.list.rows
    assert len([r for r in rows if r.kind == "item"]) == 5
    assert [r for r in rows if r.kind == "band"]  # 1.2: show bands, not folder headings
    assert view.list.winfo_ismapped() and not view.empty_state.winfo_ismapped()
    assert "5 files" in view.count_label.cget("text")


async def test_format_change_updates_titles(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.format_entry.delete(0, "end")
    view.format_entry.insert(0, "{t} [{ny}]")
    view.format_entry.event_generate("<KeyRelease>")
    await pump(tk_root, 0.3)
    [item] = [r for r in view.list.rows if r.kind == "item"]
    assert item.title == "Dune [Dune (2021)].mkv"
    assert "Dune [Dune (2021)].mkv" in view.preview_line.cget("text")


async def test_unknown_variable_flagged(tk_root: DnDCTk, view: QueueView, tmp_path: Path) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.set_format("{ny} {oops}")
    await pump(tk_root, 0.3)
    assert "{oops}" in view.preview_line.cget("text")


async def test_forge_renames_and_shows_done(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv", "Heat.1995.1080p.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    await view.forge()
    await pump(tk_root, 0.2)
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == [
        "Dune (2021).mkv",
        "Heat (1995).mkv",
    ]
    assert all(i.status is ItemStatus.DONE for i in services.queue.items)
    assert {r.status for r in view.list.rows if r.kind == "item"} == {"done"}
    assert len(await services.history.list_runs()) == 1
    assert "Forge" in view.forge_button.cget("text")


async def test_preview_writes_plan_to_log_and_touches_nothing(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    services.log_bridge.drain()
    await view.preview()
    texts = [line.text for line in services.log_bridge.drain()]
    assert any(t.startswith("Rename: Dune.2021.mkv → ") for t in texts)
    assert [p.name for p in (tmp_path / "in").iterdir()] == ["Dune.2021.mkv"]
    assert await services.history.list_runs() == []


async def test_uncertain_items_prompt_before_forge(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    [item] = services.queue.items
    item.resolution = Resolution(media=ResolvedMedia.offline(item.parsed, MatchState.UNCERTAIN))
    item.status = ItemStatus.NEEDS_CHOICE
    asked: list[str] = []

    async def answer_cancel(title: str, message: str, choices: Sequence[str]) -> str | None:
        asked.append(title)
        return "Cancel"

    view.ask = answer_cancel
    await view.forge()
    assert asked and (tmp_path / "in" / "Dune.2021.mkv").exists()

    async def answer_top(title: str, message: str, choices: Sequence[str]) -> str | None:
        return "Use top results"

    view.ask = answer_top
    await view.forge()
    assert (tmp_path / "in" / "Dune (2021).mkv").exists()


async def test_shortcuts_ignored_in_entry(tk_root: DnDCTk, view: QueueView, tmp_path: Path) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    tk_root.deiconify()
    view.format_entry.focus_force()
    await pump(tk_root, 0.1)
    assert view.shortcut("preview") is False
    view.list.canvas.focus_force()
    await pump(tk_root, 0.1)
    assert view.shortcut("preview") is True
    await pump(tk_root, 0.1)


async def test_up_down_moves_selection(tk_root: DnDCTk, view: QueueView, tmp_path: Path) -> None:
    make_files(tmp_path / "in", ["A.2001.mkv", "B.2002.mkv", "C.2003.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    keys = view.list.item_keys()
    view.move_selection(1)
    assert view.list.selected == keys[0]
    view.move_selection(1)
    assert view.list.selected == keys[1]
    view.move_selection(-1)
    assert view.list.selected == keys[0]


async def test_responsive_with_1000_items(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    make_files(
        tmp_path / "big", [f"Show.S{1 + i // 100:02d}E{i % 100:02d}.mkv" for i in range(1000)]
    )
    task = view.spawn(services.queue.add_paths([tmp_path / "big"], recursive=True))
    slowest = 0.0
    deadline = time.monotonic() + 30
    while (not task.done() or services.queue._tasks) and time.monotonic() < deadline:  # pyright: ignore[reportPrivateUsage]
        start = time.perf_counter()
        tk_root.update()
        slowest = max(slowest, time.perf_counter() - start)
        await pump(tk_root, 0.01)
    await pump_until(tk_root, lambda: len(view.list.item_keys()) == 1000, within=10)
    assert slowest < 0.1


async def test_shortcut_actions(tk_root: DnDCTk, view: QueueView) -> None:
    calls: list[str] = []

    async def fake(name: str) -> None:
        calls.append(name)

    view.preview = lambda: fake("preview")  # type: ignore[method-assign]
    view.forge = lambda: fake("forge")  # type: ignore[method-assign]
    view.undo_latest = lambda: fake("undo")  # type: ignore[method-assign]
    tk_root.deiconify()
    view.list.canvas.focus_force()
    await pump(tk_root, 0.05)
    for action in ("preview", "forge", "undo"):
        assert view.shortcut(action) is True
    await pump(tk_root, 0.05)
    assert calls == ["preview", "forge", "undo"]


async def test_forge_twice_at_once_runs_once(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    # Review I1: a second click (or Ctrl+Enter) while the first run is starting.
    import asyncio

    make_files(tmp_path / "in", ["Dune.2021.mkv", "Heat.1995.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    for item in services.queue.items:  # forces a question, so both clicks get past the start
        item.resolution = Resolution(media=ResolvedMedia.offline(item.parsed, MatchState.UNCERTAIN))
        item.status = ItemStatus.NEEDS_CHOICE

    async def slow_answer(title: str, message: str, choices: Sequence[str]) -> str | None:
        await asyncio.sleep(0.1)
        return "Use top results"

    view.ask = slow_answer
    services.log_bridge.drain()
    await asyncio.gather(view.forge(), view.forge())
    runs = await services.history.list_runs()
    assert len(runs) == 1 and runs[0].file_count == 2
    errors = [line.text for line in services.log_bridge.drain() if line.level >= 40]
    assert errors == []  # a second run would fail on every file


async def test_renamed_files_keep_their_new_path(
    tk_root: DnDCTk, view: QueueView, services: Services, tmp_path: Path
) -> None:
    # Review I2: after Forge, switching preset type must not make files "to do" again.
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    await view.forge()
    view.select_preset("Series (Flat)")
    await view.settled()
    [item] = services.queue.items
    assert item.status.value == "done"
    assert item.path == tmp_path / "in" / "Dune (2021).mkv"
    assert view.pending_items() == []


async def test_close_during_forge_waits_for_the_run(
    tk_root: DnDCTk,
    view: QueueView,
    services: Services,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review I4: closing the window mid-run stops after the current file, records it, then closes.
    import time as _time

    from mediaforge.core import fsops

    make_files(tmp_path / "in", ["A.2001.mkv", "B.2002.mkv", "C.2003.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    real_move = fsops.move_file

    moving: list[Path] = []

    def slow_move(src: Path, dst: Path) -> None:
        moving.append(src)
        _time.sleep(0.3)
        real_move(src, dst)

    monkeypatch.setattr(fsops, "move_file", slow_move)
    closed: list[int] = []
    task = view.spawn(view.forge())
    await pump_until(tk_root, lambda: bool(moving))  # mid-run: a file is being moved
    assert view.request_close(lambda: closed.append(1)) is False
    await task
    await pump(tk_root, 0.1)
    assert closed == [1]
    [run] = await services.history.list_runs()
    ops = await services.history.operations(run.id)
    assert ops and all(o.state.value == "done" for o in ops)
    assert 1 <= run.file_count < 3


def bands(view: QueueView) -> list[str]:
    return [r.title for r in view.list.rows if r.kind == "band"]


def band_key(view: QueueView, title: str) -> int:
    return next(r.key for r in view.list.rows if r.kind == "band" and r.title.startswith(title))


async def test_inbox_groups_episodes_into_show_bands(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(
        tmp_path / "in",
        [
            "Ted.Lasso.S04E03.1080p.WEB.mkv",
            "Ted.Lasso.S04E05.1080p.WEB.mkv",
            "Severance.S03E02.2160p.WEB-DL.mkv",
        ],
    )
    view.select_preset("Series (Folder)")
    await add_and_settle(tk_root, view, tmp_path / "in")
    rows = view.list.rows
    assert rows[0].kind == "section" and rows[0].title == "Needs you"
    assert bands(view) == ["Severance", "Ted Lasso"]
    ted = next(r for r in rows if r.kind == "band" and r.title == "Ted Lasso")
    assert ted.subtitle.startswith("Season 4 · 2 episodes → ")
    after = rows[rows.index(ted) + 1 : rows.index(ted) + 3]
    assert [r.code for r in after] == ["S04E03", "S04E05"]
    assert after[0].faint == "Ted.Lasso.S04E03.1080p.WEB.mkv"
    assert ted.actions == (("rename", "Rename"),)


async def test_movies_are_on_the_movies_side(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review focus 4: shows and movies dropped together each land on their own side.
    counts: list[tuple[int, int]] = []
    view.on_counts = lambda needs, other: counts.append((needs, other))
    make_files(tmp_path / "in", ["Dune.2021.mkv", "Ted.Lasso.S04E03.1080p.WEB.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    assert view.kind == "series" and bands(view) == ["Ted Lasso"]
    assert counts[-1] == (2, 1)
    view.set_kind("movies")
    await pump(tk_root, 0.1)
    assert bands(view) == ["Dune (2021)"]
    assert counts[-1] == (2, 1)


async def test_the_side_with_files_is_shown_when_this_one_is_empty(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    switched: list[str] = []
    view.on_kind = switched.append
    make_files(tmp_path / "in", ["Dune.2021.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    assert switched == ["movies"] and view.kind == "movies"
    assert bands(view) == ["Dune (2021)"]


async def test_finished_items_move_to_done(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv", "Heat.1995.1080p.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    await view.forge()
    await pump(tk_root, 0.2)
    rows = view.list.rows
    assert [r.title for r in rows if r.kind == "section"] == ["Done"]
    done = [r for r in rows if r.kind == "band"]
    assert len(done) == 2 and all(r.note and not r.actions for r in done)
    lines = [r for r in rows if r.kind == "item"]
    assert {r.faint for r in lines} == {"Dune.2021.mkv", "Heat.1995.1080p.mkv"}


async def test_band_rename_renames_only_that_band(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(
        tmp_path / "in", ["Ted.Lasso.S04E03.1080p.WEB.mkv", "Severance.S03E02.2160p.WEB-DL.mkv"]
    )
    view.select_preset("Series (Flat)")
    await add_and_settle(tk_root, view, tmp_path / "in")
    await view.settled()
    view.list.on_action(band_key(view, "Severance"), "rename")
    await pump_until(tk_root, lambda: not view.busy and len(view.services.queue.pending()) == 1)
    names = sorted(p.name for p in (tmp_path / "in").iterdir())
    assert "Ted.Lasso.S04E03.1080p.WEB.mkv" in names
    assert "Severance.S03E02.2160p.WEB-DL.mkv" not in names


async def test_band_choose_opens_the_picker_for_the_unsure_item(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mediaforge.ui import queue_view

    asked: list[int] = []

    async def fake_ask(master: object, item: object, services: object, palette: object) -> str:
        asked.append(item.id)  # type: ignore[attr-defined]
        return "cancel"

    monkeypatch.setattr(queue_view.MatchPicker, "ask", fake_ask)
    make_files(tmp_path / "in", ["Shrinking.S03E01.1080p.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    [item] = view.services.queue.items
    item.status = ItemStatus.NEEDS_CHOICE
    view.refresh()
    band = next(r for r in view.list.rows if r.kind == "band")
    assert band.title == "Shrinking?" and band.actions[0] == ("choose", "Choose…")
    view.list.on_action(band.key, "choose")
    await pump(tk_root, 0.1)
    assert asked == [item.id]


async def test_inbox_fits_the_minimum_window(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review focus 5: header and bottom-bar buttons stay on screen at 1000×640.
    old = tk_root.geometry()
    tk_root.geometry("1000x640")
    try:
        make_files(tmp_path / "in", ["Dune.2021.mkv"])
        await add_and_settle(tk_root, view, tmp_path / "in")
        await pump(tk_root, 0.3)
        right = view.winfo_rootx() + view.winfo_width()
        for widget in (view.forge_button, view.preview_button, view.preset_menu):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= right
        assert view.title_label.cget("text") == "Inbox"
    finally:
        tk_root.geometry(old)
