from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest_asyncio

from mediaforge.app import Services
from mediaforge.models.history import RunSource
from mediaforge.platform.single_instance import InstanceMessage
from mediaforge.ui.confirm_window import ConfirmWindow
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.main_window import MainShell
from mediaforge.ui.router import WindowRouter
from mediaforge.ui.theme import STUDIO_DARK

from helpers.tk import pump, pump_until


def make_files(folder: Path, names: Sequence[str]) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / n for n in names]
    for p in paths:
        p.write_bytes(b"video")
    return paths


async def open_window(
    tk_root: DnDCTk, services: Services, paths: list[Path], preset: str = "Movie (Flat)"
) -> ConfirmWindow:
    win = ConfirmWindow(tk_root, services, STUDIO_DARK, paths, preset)
    await win.loaded()
    await pump(tk_root, 0.2)
    return win


@pytest_asyncio.fixture
async def shell(tk_root: DnDCTk, services: Services) -> AsyncIterator[MainShell]:
    s = MainShell(tk_root, services, STUDIO_DARK)
    s.pack(fill="both", expand=True)
    await pump(tk_root, 0.05)
    try:
        yield s
    finally:
        s.destroy()


async def test_lists_files_with_new_names(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv", "Heat.1995.1080p.mkv"])
    win = await open_window(tk_root, services, files)
    assert win.heading.cget("text") == "Rename 2 files?"
    assert sorted(win.new_names()) == ["Dune (2021).mkv", "Heat (1995).mkv"]
    assert win.selected_label.cget("text") == "2 of 2 selected"
    win.destroy()


async def test_unticked_files_untouched(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv", "Heat.1995.1080p.mkv"])
    win = await open_window(tk_root, services, files)
    win.set_ticked(files[1], False)
    assert win.rename_button.cget("text") == "Rename 1 file"
    await win.rename()
    assert sorted(p.name for p in (tmp_path / "m").iterdir()) == [
        "Dune (2021).mkv",
        "Heat.1995.1080p.mkv",
    ]
    win.destroy()


async def test_rename_creates_right_click_run(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    win = await open_window(tk_root, services, files)
    await win.rename()
    [run] = await services.history.list_runs()
    assert run.source is RunSource.RIGHT_CLICK and run.preset_name == "Movie (Flat)"
    assert "1 renamed" in win.result_label.cget("text")
    win.destroy()


async def test_undo_button_reverts(tk_root: DnDCTk, services: Services, tmp_path: Path) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    win = await open_window(tk_root, services, files)
    await win.rename()
    await win.undo()
    assert [p.name for p in (tmp_path / "m").iterdir()] == ["Dune.2021.mkv"]
    assert "put back" in win.result_label.cget("text").lower()
    win.destroy()


async def test_nothing_happens_on_cancel(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    win = await open_window(tk_root, services, files)
    win.cancel()
    assert not win.winfo_exists()
    assert [p.name for p in (tmp_path / "m").iterdir()] == ["Dune.2021.mkv"]
    assert await services.history.list_runs() == []


async def test_open_in_main_moves_items_to_main_queue(
    tk_root: DnDCTk, services: Services, shell: MainShell, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv", "Heat.1995.mkv"])
    router = WindowRouter(tk_root, shell, services, STUDIO_DARK)
    router.open_confirm("Movie (Flat)", files)
    [win] = router.confirm_windows
    await win.loaded()
    win.open_in_main()
    await pump_until(tk_root, lambda: len(services.queue.items) == 2)
    assert router.confirm_windows == []
    assert shell.current_view == "queue"


async def test_batcher_to_window_one_window_for_many_paths(
    tk_root: DnDCTk, services: Services, shell: MainShell, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", [f"Movie.{n}.20{n:02d}.mkv" for n in range(12)])
    router = WindowRouter(tk_root, shell, services, STUDIO_DARK)
    for f in files:
        router.handle(InstanceMessage(paths=[str(f)], preset="Movie (Flat)", confirm=True))
    router.flush(now=10**9)
    [win] = router.confirm_windows
    await win.loaded()
    assert len(win.new_names()) == 12
    win.destroy()


async def test_plain_launch_message_raises_main_window_and_queues_paths(
    tk_root: DnDCTk, services: Services, shell: MainShell, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    router = WindowRouter(tk_root, shell, services, STUDIO_DARK)
    tk_root.withdraw()
    router.handle(InstanceMessage(paths=[str(files[0])], preset=None, confirm=False))
    await pump_until(tk_root, lambda: len(services.queue.items) == 1)
    assert tk_root.state() == "normal"


async def test_confirm_only_launch_exits_when_last_window_closes(
    tk_root: DnDCTk, services: Services, shell: MainShell, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    exits: list[int] = []
    router = WindowRouter(tk_root, shell, services, STUDIO_DARK, confirm_only=True)
    router.quit = lambda: exits.append(1)
    router.open_confirm("Movie (Flat)", files)
    [win] = router.confirm_windows
    await win.loaded()
    win.cancel()
    assert exits == [1]


async def test_rename_twice_at_once_runs_once(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    import asyncio

    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    win = await open_window(tk_root, services, files)
    await asyncio.gather(win.rename(), win.rename())
    runs = await services.history.list_runs()
    assert len(runs) == 1
    assert "failed" not in win.result_label.cget("text")
    win.destroy()


async def test_three_hundred_files_redraw_without_freezing(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    # Review C1: one set of widgets per file froze the window for seconds on big selections.
    import time

    files = make_files(tmp_path / "m", [f"Movie.{n}.20{n % 25:02d}.mkv" for n in range(300)])
    win = await open_window(tk_root, services, files)
    assert len(win.new_names()) == 300
    start = time.perf_counter()
    win.refresh()
    tk_root.update()
    assert time.perf_counter() - start < 0.25
    win.destroy()


async def test_clicking_a_tick_box_unticks_that_file(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv", "Heat.1995.mkv"])
    win = await open_window(tk_root, services, files)
    first, second = (item.id for item in win.queue.items)
    x, y = win.list.check_point(second), win.list.row_middle(second)
    win.list.canvas.event_generate("<Button-1>", x=x, y=y)
    await pump(tk_root, 0.05)
    assert win.chosen_ids() == [first]
    assert win.selected_label.cget("text") == "1 of 2 selected"
    win.destroy()


async def test_change_match_needs_a_chosen_row(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    files = make_files(tmp_path / "m", ["Dune.2021.mkv"])
    win = await open_window(tk_root, services, files)
    assert win.change_button.cget("state") == "disabled"
    win.list.select(win.queue.items[0].id)
    assert win.change_button.cget("state") == "normal"
    win.destroy()
