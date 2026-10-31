import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
import pytest_asyncio

from mediaforge.app import Services
from mediaforge.core.matcher import Resolution
from mediaforge.core.queue import ItemStatus
from mediaforge.models.media import MatchState, MediaKind, ResolvedMedia
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.images import ImageCache
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
    [run] = await services.history.list_runs()
    assert run.kinds == {MediaKind.MOVIE}  # History's Series | Movies switch uses this
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
    assert [r.title for r in rows if r.kind == "section"] == ["Done today"]
    done = [r for r in rows if r.kind == "band"]
    assert len(done) == 2 and all(r.note and r.actions == (("undo", "Undo"),) for r in done)
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


async def test_episode_code_shows_whatever_the_naming_rule(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Found in a real-window screenshot: with "{ny}" (Auto) the lines lost their S17E03.
    make_files(tmp_path / "in", ["Gold Rush S17E03 Diagnosing Rick 1080p AMZN WEB-DL.mkv"])
    view.select_preset("Auto")
    await add_and_settle(tk_root, view, tmp_path / "in")
    [line] = [r for r in view.list.rows if r.kind == "item"]
    assert line.code == "S17E03"


async def show_with_pictures(tk_root: DnDCTk, view: QueueView, folder: Path) -> list[str]:
    """One show band whose match has a poster and a backdrop; returns what was fetched."""
    import io

    from PIL import Image

    fetched: list[str] = []

    async def fetch(path: str, size: str) -> bytes:
        fetched.append(path)
        out = io.BytesIO()
        Image.new("RGB", (300, 170), (90, 90, 90)).save(out, format="PNG")
        return out.getvalue()

    view.images = ImageCache(fetch)
    make_files(folder, ["Ted.Lasso.S04E03.1080p.WEB.mkv"])
    await add_and_settle(tk_root, view, folder)
    for item in view.services.queue.items:
        media = item.media.model_copy(update={"poster_path": "/p.jpg", "backdrop_path": "/b.jpg"})
        item.resolution = Resolution(media=media)
    view.refresh()
    await pump(tk_root, 0.3)
    return fetched


def band_pictures(view: QueueView) -> int:
    return len(view.list.canvas.find_withtag("band-image"))


async def test_band_pictures_survive_a_resize(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review: making the window wider left only the poster; the backdrop never came back.
    await show_with_pictures(tk_root, view, tmp_path / "in")
    assert band_pictures(view) == 2
    old = tk_root.geometry()
    width, height = tk_root.winfo_width(), tk_root.winfo_height()
    tk_root.geometry(f"{width + 300}x{height}")
    try:
        await pump(tk_root, 0.4)
        assert band_pictures(view) == 2
    finally:
        tk_root.geometry(old)


async def test_pictures_come_back_after_the_cache_drops_them(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review: a picture pushed out of the cache (200 others seen since) was never fetched again.
    fetched = await show_with_pictures(tk_root, view, tmp_path / "in")
    view.images._photos.clear()  # pyright: ignore[reportPrivateUsage]
    view.list.refresh_images()
    await pump(tk_root, 0.3)
    assert band_pictures(view) == 2
    assert len(fetched) == 4  # each picture fetched once more, not over and over


async def test_clicking_an_empty_side_stays_there(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review: with only episodes in the Inbox, clicking "Movies" jumped straight back.
    switched: list[str] = []
    view.on_kind = switched.append
    make_files(tmp_path / "in", ["Ted.Lasso.S04E03.1080p.WEB.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.set_kind("movies")
    view.refresh()  # any later refresh: a lookup finishing, the naming rule changing
    await pump(tk_root, 0.2)
    assert view.kind == "movies" and switched == []
    assert view.empty_state.winfo_ismapped()
    assert view.empty_title.cget("text") == "No movies in the Inbox"


async def test_a_band_with_several_seasons_names_them_and_their_shared_folder(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    # Review: "Seasons 1–3 → … › Season 01" for seasons 1 and 3, as if all went to Season 01.
    make_files(tmp_path / "in", ["VisionQuest.S01E01.mkv", "VisionQuest.S03E02.mkv"])
    view.select_preset("Series (Folder)")
    await add_and_settle(tk_root, view, tmp_path / "in")
    [band] = [r for r in view.list.rows if r.kind == "band"]
    assert band.subtitle.startswith("Seasons 1, 3 · 2 episodes → VisionQuest")
    assert "Season 01" not in band.subtitle


# --- watched downloads in the Inbox (1.3) ------------------------------------------------------


async def library(view: QueueView, root: Path, automatic: str = "on") -> tuple[Path, Path, Path]:
    downloads, series, movies = root / "dl", root / "lib" / "Series", root / "lib" / "Movies"
    for folder in (downloads, series, movies):
        folder.mkdir(parents=True, exist_ok=True)
    services = view.services
    chosen = (await services.settings.load()).model_copy(
        update={
            "watch_folders": [str(downloads)],
            "series_folder": str(series),
            "movies_folder": str(movies),
            "automatic": automatic,
        }
    )
    await services.settings.save(chosen)  # as Settings › Library does
    services.app_settings = chosen
    return downloads, series, movies


async def watched(tk_root: DnDCTk, view: QueueView, downloads: Path, *names: str) -> list[int]:
    make_files(downloads, names)
    ids = await view.services.queue.add_watched([(downloads / n, downloads) for n in names])
    await view.services.queue.wait_idle()
    view.refresh()
    await pump(tk_root, 0.1)
    return ids


def sections(view: QueueView) -> list[str]:
    return [r.title for r in view.list.rows if r.kind == "section"]


async def test_watched_files_show_where_they_will_go(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    downloads, _, _ = await library(view, tmp_path)
    await watched(tk_root, view, downloads, "VisionQuest.S01E02.Theseus.mkv")
    [band] = [r for r in view.list.rows if r.kind == "band"]
    assert "→ Series › VisionQuest › VisionQuest - Season 01" in band.subtitle


async def test_held_files_are_under_needs_you_with_their_reason(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    downloads, _, _ = await library(view, tmp_path)
    [item_id] = await watched(tk_root, view, downloads, "Dune.mkv")
    view.services.queue.hold(item_id, "No year in the name: is it a movie?")
    view.refresh()
    await pump(tk_root, 0.1)
    assert sections(view) == ["Needs you"]
    [band] = [r for r in view.list.rows if r.kind == "band"]
    assert band.subtitle.startswith("No year in the name: is it a movie?")
    assert band.actions[-1] == ("rename", "Rename")


async def test_trial_files_are_under_would_rename(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    downloads, _, _ = await library(view, tmp_path, automatic="trial")
    await watched(tk_root, view, downloads, "Dune.2021.mkv")
    assert sections(view) == ["Would rename"]


async def test_done_today_hides_yesterday(tk_root: DnDCTk, view: QueueView, tmp_path: Path) -> None:
    make_files(tmp_path / "in", ["Dune.2021.mkv", "Heat.1995.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    queue = view.services.queue
    old, new = queue.items
    queue.mark_finished(old.id, old.path)
    queue.mark_finished(new.id, new.path)
    old.finished_at = time.time() - 2 * 24 * 3600
    view.refresh()
    await pump(tk_root, 0.1)
    assert sections(view) == ["Done today"]
    assert len([r for r in view.list.rows if r.kind == "band"]) == 1


async def test_needs_counts_only_what_needs_you(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    counts: list[tuple[int, int]] = []
    view.on_counts = lambda needs, other: counts.append((needs, other))
    downloads, _, _ = await library(view, tmp_path)
    make_files(tmp_path / "in", ["Ted.Lasso.S04E03.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")  # a dropped file: needs you
    ready, held = await watched(
        tk_root, view, downloads, "Ted.Lasso.S04E04.mkv", "Ted.Lasso.S04E05.mkv"
    )
    view.services.queue.hold(held, "TMDB doesn't know this episode.")
    view.refresh()
    await pump(tk_root, 0.1)
    assert counts[-1][0] == 2  # the dropped file and the held one, not the one about to move
    assert view.services.queue.get(ready) is not None


async def test_rename_on_a_watched_band_moves_it_to_the_library(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    downloads, _, movies = await library(view, tmp_path)
    [item_id] = await watched(tk_root, view, downloads, "Dune.2021.mkv")
    band = next(r.key for r in view.list.rows if r.kind == "band")
    view._on_band_action(band, "rename")  # pyright: ignore[reportPrivateUsage]
    target = movies / "Dune (2021)" / "Dune (2021).mkv"
    await pump_until(tk_root, target.exists)
    item = view.services.queue.get(item_id)
    assert item is not None and item.status is ItemStatus.DONE
    assert not (downloads / "Dune.2021.mkv").exists()


async def test_undo_on_a_done_band_puts_the_file_back(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Heat.1995.1080p.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    await view.forge()
    await pump(tk_root, 0.2)
    assert (tmp_path / "in" / "Heat (1995).mkv").exists()
    band = next(r.key for r in view.list.rows if r.kind == "band")
    view._on_band_action(band, "undo")  # pyright: ignore[reportPrivateUsage]
    await pump_until(tk_root, (tmp_path / "in" / "Heat.1995.1080p.mkv").exists)
    await pump(tk_root, 0.2)
    assert view.services.queue.items == []


async def test_forge_waits_for_an_automatic_run(
    tk_root: DnDCTk, view: QueueView, tmp_path: Path
) -> None:
    make_files(tmp_path / "in", ["Heat.1995.1080p.mkv"])
    await add_and_settle(tk_root, view, tmp_path / "in")
    view.select_preset("Movie (Flat)")
    await view.settled()
    lock = view.services.run_lock
    await lock.acquire()  # an automatic run is busy
    try:
        task = asyncio.get_running_loop().create_task(view.forge())
        await pump(tk_root, 0.3)
        assert (tmp_path / "in" / "Heat.1995.1080p.mkv").exists()
    finally:
        lock.release()
    await task
    assert (tmp_path / "in" / "Heat (1995).mkv").exists()
