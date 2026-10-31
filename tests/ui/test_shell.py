import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from mediaforge.app import Services, build_services, close_services, self_test
from mediaforge.platform.paths import app_paths
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.main_window import MainShell, save_window_state
from mediaforge.ui.theme import DARK, LIGHT, palette_for

from helpers.colours import contrast
from helpers.tk import pump


@pytest_asyncio.fixture
async def shell(tk_root: DnDCTk, services: Services) -> AsyncIterator[MainShell]:
    s = MainShell(tk_root, services, DARK)
    s.pack(fill="both", expand=True)
    await pump(tk_root, 0.1)
    try:
        yield s
    finally:
        s.destroy()


async def test_shell_builds_and_switches_views(tk_root: DnDCTk, shell: MainShell) -> None:
    assert shell.current_view == "queue"
    for name in ("history", "settings", "queue"):
        shell.show_view(name)
        await pump(tk_root, 0.05)
        assert shell.current_view == name
        assert shell.views[name].winfo_ismapped()
        others = [v for k, v in shell.views.items() if k != name]
        assert not any(v.winfo_ismapped() for v in others)


async def test_queue_view_is_wired_in(shell: MainShell) -> None:
    from mediaforge.ui.queue_view import QueueView

    assert isinstance(shell.views["queue"], QueueView)


async def test_history_view_is_wired_in_and_reloads_on_show(
    tk_root: DnDCTk, shell: MainShell
) -> None:
    from mediaforge.ui.history_view import HistoryView

    view = shell.views["history"]
    assert isinstance(view, HistoryView)
    shell.show_view("history")
    await pump(tk_root, 0.2)
    assert view.empty_label.winfo_manager() != ""


async def test_settings_preset_changes_reach_the_queue_dropdown(
    tk_root: DnDCTk, shell: MainShell
) -> None:
    from mediaforge.models.media import TypeHint
    from mediaforge.models.preset import Preset
    from mediaforge.ui.queue_view import QueueView
    from mediaforge.ui.settings_view import SettingsView

    settings, queue = shell.views["settings"], shell.views["queue"]
    assert isinstance(settings, SettingsView) and isinstance(queue, QueueView)
    await settings.save_preset(Preset(name="Brand New", type=TypeHint.AUTO, format="{t}"), None)
    await pump(tk_root, 0.3)
    assert "Brand New" in [p.name for p in queue.presets]


async def test_band_shows_pages_and_marks_the_current(tk_root: DnDCTk, shell: MainShell) -> None:
    assert [b.cget("text") for b in shell.page_buttons.values()] == ["Inbox", "History", "Settings"]
    shell.show_view("history")
    await pump(tk_root, 0.05)
    assert shell.page_buttons["history"].cget("text_color") == DARK.text
    assert shell.page_buttons["queue"].cget("text_color") == DARK.muted
    assert shell.page_marks["history"].cget("bg") == DARK.accent
    assert shell.page_marks["queue"].cget("bg") == DARK.rail


async def test_kind_switch_reaches_every_view(shell: MainShell) -> None:
    seen: list[tuple[str, str]] = []
    for name, view in shell.views.items():
        view.set_kind = lambda kind, n=name: seen.append((n, kind))  # type: ignore[attr-defined]
    assert shell.kind == "series" and shell.kind_switch.get() == "Series"
    shell.set_kind("movies")
    assert shell.kind == "movies" and shell.kind_switch.get().startswith("Movies")
    assert sorted(seen) == [("history", "movies"), ("queue", "movies"), ("settings", "movies")]


async def test_inbox_badge_shows_needs_count(tk_root: DnDCTk, shell: MainShell) -> None:
    # The (empty) Inbox reports its own counts as it settles; only these are under test.
    shell.views["queue"].on_counts = lambda needs, other: None  # type: ignore[attr-defined]
    shell.set_counts(needs=3, other_kind=2)
    await pump(tk_root, 0.05)
    assert shell.inbox_badge.winfo_ismapped() and shell.inbox_badge.cget("text") == "3"
    assert shell.kind_switch.cget("values") == ["Series", "Movies · 2"]
    assert shell.kind_switch.get() == "Series"
    shell.set_kind("movies")
    shell.set_counts(needs=0, other_kind=4)
    await pump(tk_root, 0.05)
    assert not shell.inbox_badge.winfo_ismapped()
    assert shell.kind_switch.cget("values") == ["Series · 4", "Movies"]
    assert shell.kind_switch.get() == "Movies"


async def test_band_fits_the_minimum_window(tk_root: DnDCTk, shell: MainShell) -> None:
    # Review focus 5: nothing in the band is pushed off at the smallest window size.
    old = tk_root.geometry()
    tk_root.geometry("1000x640")
    try:
        await pump(tk_root, 0.3)
        right = shell.winfo_rootx() + shell.winfo_width()
        for widget in (*shell.page_buttons.values(), shell.kind_switch, shell.status_label):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= right
    finally:
        tk_root.geometry(old)


async def test_log_strip_shows_records(tk_root: DnDCTk, shell: MainShell) -> None:
    logging.getLogger("mediaforge.test").error("boom happened")
    await pump(tk_root, 0.3)
    assert "boom happened" in shell.log_strip.text()


async def test_add_paths_queues_files(tk_root: DnDCTk, shell: MainShell, tmp_path: Path) -> None:
    (tmp_path / "Dune.2021.mkv").write_bytes(b"v")
    shell.show_view("history")
    await shell.add_paths([tmp_path])
    assert len(shell.services.queue.items) == 1
    assert shell.current_view == "queue"


async def test_geometry_saved(tk_root: DnDCTk, services: Services) -> None:
    await save_window_state(tk_root, services)
    settings = await services.settings.load()
    assert settings.window_geometry is not None and "x" in settings.window_geometry


def test_palettes() -> None:
    assert DARK.accent == "#4E6A85" and DARK.main == "#161616"  # AirGrabber's Slate
    assert palette_for("Light") is LIGHT
    assert palette_for("Dark") is DARK


async def test_self_test_returns_zero(tk_root: DnDCTk) -> None:
    assert await self_test(root=tk_root) == 0


async def test_build_services_imports_legacy(tmp_path: Path) -> None:
    legacy = tmp_path / "old"
    legacy.mkdir()
    config = {
        "TMDB_API_KEY": "",
        "presets": {"Only Mine": {"type": "movie", "format": "{t}", "chk": []}},
    }
    (legacy / "mediaforge_config.json").write_text(json.dumps(config), encoding="utf-8")
    paths = app_paths({"MEDIAFORGE_HOME": str(tmp_path / "home")})
    paths.ensure()
    s = await build_services(paths, legacy_dirs=[legacy])
    try:
        assert [p.name for p in await s.presets.list()] == ["Only Mine"]
        assert s.tmdb is None
    finally:
        await close_services(s)


async def test_log_strip_colours(tk_root: DnDCTk, shell: MainShell) -> None:
    log = logging.getLogger("mediaforge.test")
    log.error("bad thing")
    log.warning("odd thing")
    log.info("good thing", extra={"success": True})
    await pump(tk_root, 0.3)
    box = shell.log_strip._box  # pyright: ignore[reportPrivateUsage]
    tagged = {tag: box.get(*box.tag_ranges(tag)[:2]) for tag in ("error", "warning", "success")}
    assert "bad thing" in tagged["error"]
    assert "odd thing" in tagged["warning"]
    assert "good thing" in tagged["success"]


async def test_update_check_stages_when_automatic(
    tk_root: DnDCTk, shell: MainShell, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    fake = FakeUpdates("1.2.0")
    services.updates = fake  # type: ignore[assignment]
    await shell.check_for_update(delay_s=0)
    await pump(tk_root, 0.05)
    assert fake.staged == ["1.2.0"]
    assert shell.update_badge.winfo_ismapped()
    assert shell.update_badge.cget("text") == "⬆ Restart to update"
    shell.on_close = lambda: None
    shell.update_badge.invoke()
    assert shell.restart_requested


async def test_update_check_only_offers_when_not_automatic(
    tk_root: DnDCTk, shell: MainShell, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    fake = FakeUpdates("1.2.0")
    services.updates = fake  # type: ignore[assignment]
    services.app_settings = services.app_settings.model_copy(update={"auto_update": False})
    await shell.check_for_update(delay_s=0)
    await pump(tk_root, 0.05)
    assert fake.staged == []
    assert shell.update_badge.cget("text") == "⬆ Update to 1.2.0"
    shell.update_badge.invoke()
    assert shell.current_view == "settings"


async def test_no_update_shows_nothing(
    tk_root: DnDCTk, shell: MainShell, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    services.updates = FakeUpdates("1.1.0")  # type: ignore[assignment]
    await shell.check_for_update(delay_s=0)
    services.updates = FakeUpdates(fail=True)  # type: ignore[assignment]
    await shell.check_for_update(delay_s=0)  # GitHub unreachable: quietly logged
    await pump(tk_root, 0.05)
    assert not shell.update_badge.winfo_ismapped()


async def test_restart_waits_for_a_running_forge(shell: MainShell) -> None:
    # Review focus 4: "Update now" while Forge runs stops after the current file, then restarts.
    waiting: list[object] = []
    closed: list[int] = []
    queue = shell.views["queue"]

    def busy(close: object) -> bool:
        waiting.append(close)
        return False

    queue.request_close = busy  # type: ignore[attr-defined]
    shell.on_close = lambda: closed.append(1)
    shell.request_restart()
    assert closed == [] and not shell.restart_requested
    queue.request_close = lambda close: True  # type: ignore[attr-defined]
    waiting[0]()  # type: ignore[operator]
    assert closed == [1] and shell.restart_requested


async def test_restart_waits_for_an_undo_in_history(tk_root: DnDCTk, shell: MainShell) -> None:
    # Review: the spec says the restart waits for "Forge or Undo"; a cut-off undo could leave
    # History showing the wrong state.
    closed: list[int] = []
    history = shell.views["history"]
    history.busy = True  # type: ignore[attr-defined]
    shell.on_close = lambda: closed.append(1)
    shell.request_restart()
    await pump(tk_root, 0.5)
    assert closed == [] and not shell.restart_requested
    history.busy = False  # type: ignore[attr-defined]
    await pump(tk_root, 0.6)
    assert closed == [1] and shell.restart_requested


async def test_page_names_sit_close_together(tk_root: DnDCTk, shell: MainShell) -> None:
    # Found in a real-window screenshot: layout boxes took a default width and spread the pages.
    await pump(tk_root, 0.1)
    buttons = list(shell.page_buttons.values())
    for left, right in zip(buttons, buttons[1:], strict=False):
        gap = right.winfo_rootx() - (left.winfo_rootx() + left.winfo_width())
        assert gap < 40, gap


async def test_a_switchs_chosen_part_is_readable_in_light(
    tk_root: DnDCTk, services: Services
) -> None:
    import customtkinter as ctk

    light = palette_for("Light", "teal")
    s = MainShell(tk_root, services, light)
    s.pack(fill="both", expand=True)
    await pump(tk_root, 0.1)
    try:
        switches: list[ctk.CTkSegmentedButton] = []
        stack: list[object] = [s]
        while stack:
            widget = stack.pop()
            if isinstance(widget, ctk.CTkSegmentedButton):
                switches.append(widget)
            stack.extend(widget.winfo_children())  # type: ignore[attr-defined]
        # The Series | Movies switch, the Settings tabs and its light/dark switch at least.
        assert len(switches) >= 3
        for switch in switches:
            text, chosen = switch.cget("text_color"), switch.cget("selected_color")
            assert contrast(text, chosen) >= 4.5, (text, chosen)
            # No grey frame from customtkinter's own theme around the parts.
            assert switch.cget("fg_color") == switch.cget("unselected_color"), switch
    finally:
        s.destroy()


async def test_the_switch_is_not_rebuilt_when_its_labels_stay(shell: MainShell) -> None:
    # Review: every Inbox refresh rebuilt both halves of the switch (and could eat a click).
    shell.set_counts(needs=3, other_kind=2)
    before = list(shell.kind_switch._buttons_dict.values())  # pyright: ignore[reportPrivateUsage]
    shell.set_counts(needs=3, other_kind=2)
    shell.set_counts(needs=4, other_kind=2)
    after = list(shell.kind_switch._buttons_dict.values())  # pyright: ignore[reportPrivateUsage]
    assert after == before


async def test_the_watching_light_follows_the_status(tk_root: DnDCTk, shell: MainShell) -> None:
    from mediaforge.core.auto import WatchStatus

    shell.set_watch_status(WatchStatus("trial", "Trial: watching 1 folder"))
    await pump(tk_root, 0.05)
    assert shell.status_label.cget("text") == "Trial: watching 1 folder"
    assert shell.watch_dot.winfo_ismapped() and shell.watch_dot.cget("text_color") == DARK.warning
    shell.set_watch_status(WatchStatus("watching", "Watching 2 folders"))
    assert shell.watch_dot.cget("text_color") == DARK.success
    shell.set_watch_status(WatchStatus("unreachable", "Can't reach the NAS"))
    assert shell.watch_dot.cget("text_color") == DARK.error
    shell.open_library_settings()
    assert shell.current_view == "settings"
    shell.set_watch_status(WatchStatus("off", "Not watching"))
    await pump(tk_root, 0.05)
    assert shell.status_label.cget("text") == "Drop files here"
    assert not shell.watch_dot.winfo_ismapped()


async def test_a_staged_update_restarts_into_the_tray_when_hidden(
    tk_root: DnDCTk, shell: MainShell, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    services.updates = FakeUpdates("9.9.9")  # type: ignore[assignment]
    closed: list[int] = []
    shell.on_close = lambda: closed.append(1)
    tk_root.withdraw()  # MediaForge is in the tray
    try:
        await shell.check_for_update(delay_s=0)
        await pump(tk_root, 0.05)
    finally:
        tk_root.deiconify()
    assert shell.restart_requested and shell.restart_hidden and closed == [1]


async def test_a_staged_update_waits_while_the_window_shows(
    tk_root: DnDCTk, shell: MainShell, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    services.updates = FakeUpdates("9.9.9")  # type: ignore[assignment]
    shell.on_close = lambda: None
    await shell.check_for_update(delay_s=0)
    await pump(tk_root, 0.05)
    assert not shell.restart_requested  # the owner chooses when (the Restart to update bar)


async def test_the_update_check_repeats_daily(shell: MainShell, services: Services) -> None:
    import asyncio

    from helpers.fake_updates import FakeUpdates

    fake = FakeUpdates("1.1.0")  # the same as this copy: nothing newer
    services.updates = fake  # type: ignore[assignment]
    task = asyncio.get_running_loop().create_task(shell.keep_checking_for_updates(0, 0.02))
    await asyncio.sleep(0.15)
    task.cancel()
    assert fake.checks >= 3
