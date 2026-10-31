"""The Studio main window: icon rail on the left, the active view, the Activity strip.

MainShell holds everything inside the window, so tests can build it in any root.
MainWindow is the real top-level window the app opens once.
"""

from __future__ import annotations

import asyncio
import logging
import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import customtkinter as ctk
from async_tkinter_loop import async_handler
from PIL import Image
from tkinterdnd2 import DND_FILES

from mediaforge import launch
from mediaforge.api.updates import UpdateError
from mediaforge.platform import context_menu
from mediaforge.platform.single_instance import InstanceMessage
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.log_strip import LogStrip
from mediaforge.ui.theme import ASSETS, Palette, font
from mediaforge.ui.tray import TrayIcon, close_action

if TYPE_CHECKING:
    from mediaforge.app import Services
    from mediaforge.core.auto import WatchStatus

log = logging.getLogger("mediaforge.ui")

UPDATE_CHECK_DELAY_S = 10.0  # after start, so the update check never slows the window down
UPDATE_INTERVAL_S = 24 * 3600  # and again every day
TRAY_NOTICE = (
    "It's in the tray next to the clock. Right-click its icon and choose Quit to close it."
)

PAGES: dict[str, str] = {"queue": "Inbox", "history": "History", "settings": "Settings"}
KINDS: dict[str, str] = {"series": "Series", "movies": "Movies"}
type Kind = Literal["series", "movies"]


class MainShell(ctk.CTkFrame):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self.current_view: str = ""
        self.views: dict[str, ctk.CTkFrame] = {}
        self.page_buttons: dict[str, ctk.CTkButton] = {}
        self.page_marks: dict[str, tk.Frame] = {}
        self.kind: Kind = "series"
        self._other_kind = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self.restart_requested = False
        self.restart_hidden = False  # restart in the tray (an update while hidden)
        self._staged: str | None = None  # the version waiting to be installed
        self.on_close: Callable[[], None] = lambda: None  # MainWindow: close the window

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_band()
        self.content = ctk.CTkFrame(self, fg_color=palette.main, corner_radius=0)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self._build_update_bar()
        self.log_strip = LogStrip(self, services.log_bridge, palette)
        self.log_strip.grid(row=3, column=0, sticky="ew")

        for name, label in PAGES.items():
            self.set_view(name, self._placeholder(label))
        self.build_views()
        self.show_view("queue")

    # --- layout ----------------------------------------------------------------------------

    def _build_band(self) -> None:
        """AirGrabber's top band: name, the Series | Movies switch, the pages, the status."""
        p = self.palette
        band = ctk.CTkFrame(self, fg_color=p.rail, corner_radius=0)
        band.grid(row=0, column=0, sticky="ew")
        ctk.CTkFrame(band, fg_color=p.border, height=1, corner_radius=0).pack(
            side="bottom", fill="x"
        )
        row = ctk.CTkFrame(band, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 0))
        logo_path = ASSETS / "MediaForge.png"
        if logo_path.exists():
            image = ctk.CTkImage(Image.open(logo_path), size=(22, 21))
            ctk.CTkLabel(row, image=image, text="").pack(side="left", pady=(0, 8))
        ctk.CTkLabel(row, text="MediaForge", font=font(14, "bold"), text_color=p.text).pack(
            side="left", padx=(8, 18), pady=(0, 8)
        )
        self.kind_switch = ctk.CTkSegmentedButton(
            row,
            values=list(KINDS.values()),
            command=self._kind_clicked,
            fg_color=p.raised,
            selected_color=p.selected,
            selected_hover_color=p.selected_hover,
            unselected_color=p.raised,
            unselected_hover_color=p.border,
            text_color=p.text,
            font=font(12),
            height=28,
        )
        self.kind_switch.set(KINDS["series"])
        self.kind_switch.pack(side="left", padx=(0, 18), pady=(0, 8))
        # Plain Tk frames: a CTkFrame asks for a default 200 px and spread the pages apart.
        pages = tk.Frame(row, bg=p.rail)
        pages.pack(side="left")
        for name, label in PAGES.items():
            cell = tk.Frame(pages, bg=p.rail)
            cell.pack(side="left", padx=(0, 4))
            line = tk.Frame(cell, bg=p.rail)
            line.pack()
            button = ctk.CTkButton(
                line,
                text=label,
                width=70,
                height=30,
                corner_radius=6,
                fg_color="transparent",
                hover_color=p.raised,
                text_color=p.muted,
                font=font(13),
                command=lambda n=name: self.show_view(n),
            )
            button.pack(side="left")
            if name == "queue":
                self.inbox_badge = ctk.CTkLabel(
                    line,
                    text="",
                    width=20,
                    height=18,
                    corner_radius=9,
                    fg_color=p.error,
                    text_color=p.on_color,
                    font=font(10, "bold"),
                )
            mark = tk.Frame(cell, bg=p.rail, height=3)
            mark.pack(fill="x", padx=10, pady=(2, 0))
            self.page_buttons[name] = button
            self.page_marks[name] = mark
        self.status_label = ctk.CTkLabel(
            row, text="Drop files here", font=font(11), text_color=p.muted
        )
        self.status_label.pack(side="right", pady=(0, 8))
        self.status_label.bind("<Button-1>", lambda e: self.open_library_settings())
        # The watching light (1.3): left of the status text while renaming on its own is set up.
        self.watch_dot = ctk.CTkLabel(row, text="●", font=font(12), text_color=p.muted)
        self.watch_dot.bind("<Button-1>", lambda e: self.open_library_settings())

    def set_watch_status(self, status: WatchStatus) -> None:
        """Watching, trial, can't reach the NAS, or not set up: a coloured dot and a few words."""
        p = self.palette
        if status.state == "off":
            self.status_label.configure(text="Drop files here", cursor="")
            self.watch_dot.pack_forget()
            return
        colour = {"watching": p.success, "trial": p.warning, "unreachable": p.error}
        self.status_label.configure(text=status.text, cursor="hand2")
        self.watch_dot.configure(text_color=colour.get(status.state, p.muted))
        self.watch_dot.pack(side="right", padx=(0, 6), pady=(0, 8))

    def open_library_settings(self) -> None:
        self.show_view("settings")
        settings = self.views.get("settings")
        tabs = getattr(settings, "tabs", None)
        if tabs is not None and "Library" in getattr(settings, "tab_names", ()):
            tabs.set("Library")

    def _kind_clicked(self, value: str) -> None:
        self.set_kind("movies" if value.startswith(KINDS["movies"]) else "series")

    def set_kind(self, kind: Kind) -> None:
        """Show series or movies in every view that tells them apart."""
        self.kind = kind
        self._label_kinds()
        for view in self.views.values():
            set_kind = getattr(view, "set_kind", None)
            if callable(set_kind):
                set_kind(kind)

    def set_counts(self, needs: int, other_kind: int) -> None:
        """The Inbox's count of what needs the owner, and how many files the other side has."""
        if needs > 0:
            self.inbox_badge.configure(text=str(needs))
            self.inbox_badge.pack(side="left", padx=(0, 2))
        else:
            self.inbox_badge.pack_forget()
        self._other_kind = other_kind
        self._label_kinds()

    def _label_kinds(self) -> None:
        labels = []
        for kind, label in KINDS.items():
            if kind != self.kind and self._other_kind:
                label = f"{label} · {self._other_kind}"
            labels.append(label)
        if labels != self.kind_switch.cget("values"):  # configuring rebuilds both halves
            self.kind_switch.configure(values=labels)
        self.kind_switch.set(labels[list(KINDS).index(self.kind)])

    def _build_update_bar(self) -> None:
        """A thin bar above the Activity strip, shown only when an update is waiting."""
        p = self.palette
        self.update_bar = ctk.CTkFrame(self, fg_color=p.raised, corner_radius=0)
        self.update_text = ctk.CTkLabel(
            self.update_bar, text="", font=font(12), text_color=p.text, anchor="w"
        )
        self.update_text.pack(side="left", padx=12, pady=4)
        self.update_badge = ctk.CTkButton(
            self.update_bar,
            text="",
            height=26,
            fg_color=p.accent,
            hover_color=p.accent_hover,
            text_color=p.accent_text,
            font=font(12, "bold"),
        )
        self.update_badge.pack(side="right", padx=12, pady=4)

    def _show_update(self, text: str, button: str, command: Callable[[], None]) -> None:
        self.update_text.configure(text=text)
        self.update_badge.configure(text=button, command=command)
        self.update_bar.grid(row=2, column=0, sticky="ew")

    def _placeholder(self, title: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color=self.palette.main, corner_radius=0)
        ctk.CTkLabel(frame, text=title, font=font(16, "bold"), text_color=self.palette.muted).pack(
            padx=20, pady=20, anchor="nw"
        )
        return frame

    def build_views(self) -> None:
        """Create the real views; until a view exists, its placeholder stands in."""
        from mediaforge.ui.history_view import HistoryView
        from mediaforge.ui.queue_view import QueueView
        from mediaforge.ui.settings_view import SettingsView

        queue = QueueView(self.content, self.services, self.palette)
        settings = SettingsView(self.content, self.services, self.palette)

        def presets_changed() -> None:
            queue.reload_presets()
            self.spawn(self.refresh_menu())

        settings.on_presets_changed = presets_changed
        settings.on_restart = self.request_restart
        queue.on_counts = self.set_counts
        queue.on_kind = self.set_kind
        self.set_view("queue", queue)
        self.set_view("history", HistoryView(self.content, self.services, self.palette))
        self.set_view("settings", settings)

    def set_view(self, name: str, frame: ctk.CTkFrame) -> None:
        old = self.views.get(name)
        if old is not None and old is not frame:
            old.destroy()
        self.views[name] = frame
        if name == self.current_view:
            frame.grid(row=0, column=0, sticky="nsew")

    def show_view(self, name: str) -> None:
        for key, frame in self.views.items():
            if key == name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        for key, button in self.page_buttons.items():
            active = key == name
            button.configure(text_color=self.palette.text if active else self.palette.muted)
            self.page_marks[key].configure(bg=self.palette.accent if active else self.palette.rail)
        self.current_view = name
        on_show = getattr(self.views[name], "on_show", None)
        if callable(on_show):
            on_show()

    # --- behaviour -------------------------------------------------------------------------

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        """Run a coroutine on the app's event loop, keeping a reference until it ends."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def add_paths(self, paths: Sequence[Path]) -> None:
        recursive = self.services.app_settings.scan_subfolders
        added = await self.services.queue.add_paths(list(paths), recursive)
        self.show_view("queue")
        if added:
            log.info("Added %d file(s) to the queue", added)

    async def check_for_update(self, delay_s: float = UPDATE_CHECK_DELAY_S) -> None:
        """Once after start, as in AirGrabber: download a newer version in the background so it
        installs when MediaForge closes, or (automatic updates off) offer it."""
        await asyncio.sleep(delay_s)
        updates = self.services.updates
        try:
            info = await updates.check()
        except UpdateError as exc:
            log.info("Update check: %s", exc)
            return
        if not info.available or not self.winfo_exists():
            return
        if not info.installable:
            log.info("MediaForge %s is on GitHub; development copies don't update", info.latest)
            return
        offer = (f"MediaForge {info.latest} is available.", f"⬆ Update to {info.latest}")
        if not self.services.app_settings.auto_update:
            self._show_update(*offer, lambda: self.show_view("settings"))
            return
        hidden = self.winfo_toplevel().state() == "withdrawn"
        if self._staged == info.latest:
            version = info.latest  # downloaded at an earlier check
        else:
            try:
                version = await updates.stage()
            except UpdateError as exc:
                log.warning("Downloading the update failed: %s", exc)
                if self.winfo_exists():  # Settings can still install it
                    self._show_update(*offer, lambda: self.show_view("settings"))
                return
            self._staged = version
        if not self.winfo_exists():
            return
        if hidden:  # in the tray: restart into the tray now, so the update installs
            log.info("Restarting into the tray to install MediaForge %s", version)
            self.restart_hidden = True
            self.request_restart()
            return
        self._show_update(
            f"MediaForge {version} is ready. It installs when you close MediaForge.",
            "⬆ Restart to update",
            self.request_restart,
        )
        log.info("MediaForge %s is ready; it installs when you close MediaForge", version)

    async def keep_checking_for_updates(
        self, delay_s: float = UPDATE_CHECK_DELAY_S, interval_s: float = UPDATE_INTERVAL_S
    ) -> None:
        """Check after start, then every day: MediaForge may sit in the tray for weeks."""
        while not self.restart_requested:
            await self.check_for_update(delay_s)
            await asyncio.sleep(interval_s)

    async def refresh_menu(self) -> None:
        """When the right-click menu is on, point it at this copy and the current presets."""
        names = [p.name for p in await self.services.presets.list()]
        chosen = self.services.app_settings.context_menu_presets
        try:
            if await asyncio.to_thread(context_menu.refresh, names, chosen, launch.LAUNCHER):
                log.debug("Right-click menu refreshed")
        except OSError as exc:
            log.warning("Couldn't refresh the right-click menu: %s", exc)

    def may_close(self, retry: Callable[[], None]) -> bool:
        """True if the window may close now. Otherwise `retry` is called later: a running Forge
        stops after its current file first, and an undo in History is allowed to finish."""
        queue = self.views.get("queue")
        request_close = getattr(queue, "request_close", None)
        if callable(request_close) and not request_close(retry):
            return False
        if getattr(self.views.get("history"), "busy", False):
            log.info("Waiting for the undo to finish…")
            self.after(250, retry)
            return False
        return True

    def request_restart(self) -> None:
        """Close and start again (to update), exactly as when the window is closed."""
        if not self.may_close(self.request_restart):
            return
        self.restart_requested = True
        self.on_close()

    def select_preset(self, name: str) -> None:
        select = getattr(self.views.get("queue"), "select_preset", None)
        if callable(select):
            select(name)

    def handle_instance_message(self, msg: InstanceMessage) -> None:
        """Another MediaForge launch handed its files to this window."""
        top = self.winfo_toplevel()
        top.deiconify()
        top.lift()
        top.focus_force()
        if msg.preset:
            self.select_preset(msg.preset)
        if msg.paths:
            self.spawn(self.add_paths([Path(p) for p in msg.paths]))

    def destroy(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        super().destroy()


async def save_window_state(window: ctk.CTk, services: Services) -> None:
    settings = await services.settings.load()
    if window.state() == "normal":
        settings = settings.model_copy(update={"window_geometry": window.geometry()})
    await services.settings.save(settings)
    services.app_settings = settings


class MainWindow(DnDCTk):
    """The one real top-level window."""

    def __init__(self, services: Services, palette: Palette) -> None:
        super().__init__()
        self.services = services
        self.title("MediaForge")
        self.minsize(1000, 640)
        self.configure(fg_color=palette.main)
        icon = ASSETS / "MediaForge.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                log.debug("Window icon not set", exc_info=True)
        self.geometry(services.app_settings.window_geometry or "1200x800")
        self.shell = MainShell(self, services, palette)
        self.shell.pack(fill="both", expand=True)
        self.shell.on_close = lambda: self.shell.spawn(self.quit_app())
        self.tray: TrayIcon | None = None  # set by app.run when the tray icon works
        self.drop_target_register(DND_FILES)  # pyright: ignore[reportUnknownMemberType]
        self.dnd_bind("<<Drop>>", self._on_drop)  # pyright: ignore[reportUnknownMemberType]
        self.protocol("WM_DELETE_WINDOW", async_handler(self.close))

    def _on_drop(self, event: Any) -> str:
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        if paths:
            self.shell.spawn(self.shell.add_paths(paths))
        return str(event.action)

    async def close(self) -> None:
        """The window's close button: into the tray while watching, otherwise quit."""
        running = self.tray is not None and self.tray.running
        settings = self.services.app_settings
        if close_action(automatic=settings.automatic, tray_running=running) == "quit":
            await self.quit_app()
            return
        await save_window_state(self, self.services)
        self.withdraw()
        if not settings.tray_notice_shown and self.tray is not None:
            self.tray.notify("MediaForge keeps watching", TRAY_NOTICE)
            stored = await self.services.settings.load()
            await self.services.settings.save(stored.model_copy(update={"tray_notice_shown": True}))
            self.services.app_settings = settings.model_copy(update={"tray_notice_shown": True})

    async def quit_app(self) -> None:
        """Really close MediaForge (the tray's Quit, a restart to update, or close when not
        watching)."""
        if not self.shell.may_close(lambda: self.shell.spawn(self.quit_app())):
            return  # a Forge stops after its current file, an undo finishes; then it closes
        await save_window_state(self, self.services)
        self.destroy()
