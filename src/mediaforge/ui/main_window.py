"""The Studio main window: icon rail on the left, the active view, the Activity strip.

MainShell holds everything inside the window, so tests can build it in any root.
MainWindow is the real top-level window the app opens once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")

UPDATE_CHECK_DELAY_S = 10.0  # after start, so the update check never slows the window down

VIEW_ICONS: dict[str, tuple[str, str]] = {
    "queue": ("☰", "Queue"),
    "history": ("⟲", "History"),
    "settings": ("⚙", "Settings"),
}


class MainShell(ctk.CTkFrame):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self.current_view: str = ""
        self.views: dict[str, ctk.CTkFrame] = {}
        self.rail_buttons: dict[str, ctk.CTkButton] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self.restart_requested = False
        self.on_close: Callable[[], None] = lambda: None  # MainWindow: close the window

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_rail()
        self.content = ctk.CTkFrame(self, fg_color=palette.main, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self._build_update_bar()
        self.log_strip = LogStrip(self, services.log_bridge, palette)
        self.log_strip.grid(row=2, column=1, sticky="ew")

        for name, (_, label) in VIEW_ICONS.items():
            self.set_view(name, self._placeholder(label))
        self.build_views()
        self.show_view("queue")

    # --- layout ----------------------------------------------------------------------------

    def _build_rail(self) -> None:
        p = self.palette
        rail = ctk.CTkFrame(self, width=52, fg_color=p.rail, corner_radius=0)
        rail.grid(row=0, column=0, rowspan=3, sticky="ns")
        rail.grid_propagate(False)
        logo_path = ASSETS / "MediaForge.png"
        if logo_path.exists():
            image = ctk.CTkImage(Image.open(logo_path), size=(32, 31))
            ctk.CTkLabel(rail, image=image, text="").pack(pady=(14, 18))
        for name, (glyph, label) in VIEW_ICONS.items():
            button = ctk.CTkButton(
                rail,
                text=glyph,
                width=36,
                height=36,
                corner_radius=8,
                fg_color="transparent",
                hover_color=p.raised,
                text_color=p.muted,
                font=font(17),
                command=lambda n=name: self.show_view(n),
            )
            button.pack(pady=4)
            self.rail_buttons[name] = button
            _Tooltip(button, label, p)

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
            hover_color=p.warning,
            text_color=p.accent_text,
            font=font(12, "bold"),
        )
        self.update_badge.pack(side="right", padx=12, pady=4)

    def _show_update(self, text: str, button: str, command: Callable[[], None]) -> None:
        self.update_text.configure(text=text)
        self.update_badge.configure(text=button, command=command)
        self.update_bar.grid(row=1, column=1, sticky="ew")

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
        for key, button in self.rail_buttons.items():
            active = key == name
            button.configure(
                fg_color=self.palette.raised if active else "transparent",
                text_color=self.palette.accent if active else self.palette.muted,
            )
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
        try:
            version = await updates.stage()
        except UpdateError as exc:
            log.warning("Downloading the update failed: %s", exc)
            if self.winfo_exists():  # Settings can still install it
                self._show_update(*offer, lambda: self.show_view("settings"))
            return
        if not self.winfo_exists():
            return
        self._show_update(
            f"MediaForge {version} is ready. It installs when you close MediaForge.",
            "⬆ Restart to update",
            self.request_restart,
        )
        log.info("MediaForge %s is ready; it installs when you close MediaForge", version)

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
        self.shell.on_close = lambda: self.shell.spawn(self.close())
        self.drop_target_register(DND_FILES)  # pyright: ignore[reportUnknownMemberType]
        self.dnd_bind("<<Drop>>", self._on_drop)  # pyright: ignore[reportUnknownMemberType]
        self.protocol("WM_DELETE_WINDOW", async_handler(self.close))

    def _on_drop(self, event: Any) -> str:
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        if paths:
            self.shell.spawn(self.shell.add_paths(paths))
        return str(event.action)

    async def close(self) -> None:
        if not self.shell.may_close(lambda: self.shell.spawn(self.close())):
            return  # a Forge stops after its current file, an undo finishes; then it closes
        await save_window_state(self, self.services)
        self.destroy()


class _Tooltip:
    """A small label shown next to a rail button while the mouse is over it."""

    def __init__(self, widget: ctk.CTkBaseClass, text: str, palette: Palette) -> None:
        self._widget = widget
        self._text = text
        self._palette = palette
        self._tip: ctk.CTkToplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: Any = None) -> None:
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() + 6
        y = self._widget.winfo_rooty() + 6
        tip = ctk.CTkToplevel(self._widget)
        tip.overrideredirect(True)
        tip.geometry(f"+{x}+{y}")
        ctk.CTkLabel(
            tip,
            text=self._text,
            fg_color=self._palette.raised,
            text_color=self._palette.text,
            corner_radius=6,
            font=font(11),
        ).pack(ipadx=6, ipady=2)
        self._tip = tip

    def _hide(self, _event: Any = None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
