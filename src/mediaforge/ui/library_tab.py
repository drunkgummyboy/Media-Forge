"""Settings › Library: where downloads come from and where shows and movies go (1.3).

Every folder is chosen here, nothing is assumed: the download folders to watch, the Series and
Movies library folders and the preset each uses, renaming on its own (Off / Trial / On), clean-up,
start with Windows, and the two notifications. Each change is saved at once and handed to the
automatic renamer.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any, Literal

import customtkinter as ctk

from mediaforge.core.library import library_problems
from mediaforge.db.settings import AppSettings
from mediaforge.platform import startup
from mediaforge.ui.theme import font

if TYPE_CHECKING:
    from mediaforge.ui.settings_view import SettingsView

log = logging.getLogger("mediaforge.ui")

MODES = {"Off": "off", "Trial": "trial", "On": "on"}
TRIAL_FIRST = "Trial first: MediaForge shows what it would do; switch to On when you're happy."
_CLEAN_TEXT = "Clean up downloads after moving (samples, .nfo, .txt, .url, empty folders)"


class LibraryTab:
    """Builds the Library tab's sections into the Settings view and keeps them up to date."""

    def __init__(self, view: SettingsView) -> None:
        self.view = view
        self.services = view.services
        p = self.palette = view.palette
        self.ask_folder: Callable[[str], str] = lambda title: filedialog.askdirectory(
            title=title, parent=view, mustexist=True
        )
        self.startup_enabled: Callable[[], bool] = startup.is_enabled
        self.set_startup: Callable[[bool], Awaitable[None]] = startup.set_enabled

        box = view._section(  # pyright: ignore[reportPrivateUsage]
            "Download folders to watch",
            "MediaForge looks here every few minutes for downloads that have finished, such as "
            "AirGrabber's download folder.",
        )
        self.watch_list = ctk.CTkFrame(box, fg_color="transparent")
        self.watch_list.pack(fill="x")
        view._button(  # pyright: ignore[reportPrivateUsage]
            box, "Add folder…", lambda: view.spawn(self.add_watch_folder())
        ).pack(anchor="w", pady=(6, 0))

        box = view._section(  # pyright: ignore[reportPrivateUsage]
            "Library folders",
            "Shows and movies are moved here, each named with its preset.",
        )
        self.folder_labels: dict[str, ctk.CTkLabel] = {}
        self.preset_menus: dict[str, ctk.CTkOptionMenu] = {}
        for row, (kind, title) in enumerate((("series", "Series"), ("movies", "Movies"))):
            ctk.CTkLabel(box, text=title, font=font(12, "bold"), text_color=p.text).grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=3
            )
            label = ctk.CTkLabel(box, text="", font=font(12), text_color=p.muted, anchor="w")
            label.grid(row=row, column=1, sticky="w", padx=(0, 12))
            view._button(  # pyright: ignore[reportPrivateUsage]
                box, "Choose…", lambda k=kind: view.spawn(self.choose_library(k)), width=90
            ).grid(row=row, column=2, padx=(0, 12))
            menu = ctk.CTkOptionMenu(
                box,
                values=["…"],
                width=170,
                fg_color=p.raised,
                button_color=p.raised,
                button_hover_color=p.border,
                text_color=p.text,
                dropdown_fg_color=p.surface,
                dropdown_text_color=p.text,
                font=font(12),
                command=lambda name, k=kind: view.spawn(self.set_preset(k, name)),
            )
            menu.grid(row=row, column=3)
            self.folder_labels[kind] = label
            self.preset_menus[kind] = menu
        box.grid_columnconfigure(1, weight=1)

        box = view._section(  # pyright: ignore[reportPrivateUsage]
            "Rename on its own",
            "Sure matches are renamed and moved by themselves; anything else waits in the Inbox. "
            "Trial shows what would happen without moving anything.",
        )
        self.mode = ctk.CTkSegmentedButton(
            box,
            values=list(MODES),
            fg_color=p.raised,
            selected_color=p.selected,
            selected_hover_color=p.selected_hover,
            unselected_color=p.raised,
            unselected_hover_color=p.border,
            text_color=p.text,
            font=font(12),
            command=lambda value: view.spawn(self.set_mode(value)),
        )
        self.mode.pack(anchor="w")
        self.note_label = ctk.CTkLabel(box, text="", font=font(11), text_color=p.muted)
        self.note_label.pack(anchor="w", pady=(4, 0))
        self.problems_label = ctk.CTkLabel(
            box, text="", font=font(11), text_color=p.warning, justify="left"
        )
        self.problems_label.pack(anchor="w")

        box = view._section("Options")  # pyright: ignore[reportPrivateUsage]
        self.clean_box = self._check(box, _CLEAN_TEXT, "clean_downloads")
        self.startup_box = self._check(box, "Start with Windows (in the tray)", None)
        self.startup_box.configure(command=lambda: view.spawn(self.toggle_startup()))
        self.needs_box = self._check(box, "Tell me when files need me", "notify_needs")
        self.summary_box = self._check(box, "A summary in the evening", "daily_summary")
        self.fill(self.services.app_settings, [])

    def _check(self, master: Any, text: str, key: str | None) -> ctk.CTkCheckBox:
        p = self.palette
        box = ctk.CTkCheckBox(
            master,
            text=text,
            font=font(12),
            text_color=p.text,
            fg_color=p.accent,
            hover_color=p.accent_hover,
            border_color=p.edge,
            checkmark_color=p.accent_text,
        )
        if key is not None:
            box.configure(command=lambda: self.view.spawn(self._save(**{key: bool(box.get())})))
        box.pack(anchor="w", pady=2)
        return box

    # --- showing the settings ------------------------------------------------------------------

    def fill(self, settings: AppSettings, preset_names: list[str]) -> None:
        """Show `settings` (and the preset names, when known)."""
        p = self.palette
        for child in self.watch_list.winfo_children():
            child.destroy()
        if not settings.watch_folders:
            ctk.CTkLabel(self.watch_list, text="None yet", font=font(12), text_color=p.muted).pack(
                anchor="w"
            )
        for folder in settings.watch_folders:
            row = ctk.CTkFrame(self.watch_list, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=folder, font=font(12), text_color=p.text).pack(side="left")
            self.view._button(  # pyright: ignore[reportPrivateUsage]
                row,
                "Remove",
                lambda f=folder: self.view.spawn(self.remove_watch_folder(f)),
                width=80,
            ).pack(side="right")
        for kind, folder in (
            ("series", settings.series_folder),
            ("movies", settings.movies_folder),
        ):
            self.folder_labels[kind].configure(text=folder or "Not chosen yet")
        if preset_names:
            for menu in self.preset_menus.values():
                menu.configure(values=preset_names)
        self.preset_menus["series"].set(settings.series_preset)
        self.preset_menus["movies"].set(settings.movies_preset)
        self.mode.set(next(k for k, v in MODES.items() if v == settings.automatic))
        problems = library_problems(settings)
        self.mode.configure(state="disabled" if problems else "normal")
        self.problems_label.configure(text="\n".join(problems))
        for box, on in (
            (self.clean_box, settings.clean_downloads),
            (self.needs_box, settings.notify_needs),
            (self.summary_box, settings.daily_summary),
            (self.startup_box, self.startup_enabled()),
        ):
            box.select() if on else box.deselect()

    # --- changes -------------------------------------------------------------------------------

    async def _save(self, **changes: Any) -> AppSettings:
        services = self.services
        settings = (await services.settings.load()).model_copy(update=changes)
        await services.settings.save(settings)
        services.app_settings = settings
        self.fill(settings, [p.name for p in getattr(self.view, "presets", [])])
        services.auto.reconfigure()
        return settings

    async def add_watch_folder(self) -> None:
        chosen = self.ask_folder("Choose a download folder to watch")
        if not chosen:
            return
        folder = str(Path(chosen))
        folders = list(self.services.app_settings.watch_folders)
        if folder not in folders:
            await self._save(watch_folders=[*folders, folder])

    async def remove_watch_folder(self, folder: str) -> None:
        folders = [f for f in self.services.app_settings.watch_folders if f != folder]
        await self._save(watch_folders=folders)

    async def choose_library(self, kind: Literal["series", "movies"]) -> None:
        title = (
            "Choose the Series library folder"
            if kind == "series"
            else "Choose the Movies library folder"
        )
        chosen = self.ask_folder(title)
        if chosen:
            await self._save(**{f"{kind}_folder": str(Path(chosen))})

    async def set_preset(self, kind: str, name: str) -> None:
        await self._save(**{f"{kind}_preset": name})

    async def set_mode(self, label: str) -> None:
        mode = MODES[label]
        note = ""
        if mode == "on" and self.services.app_settings.automatic == "off":
            mode, note = "trial", TRIAL_FIRST  # see what it would do before it moves anything
        await self._save(automatic=mode)
        self.note_label.configure(text=note)

    async def toggle_startup(self) -> None:
        on = bool(self.startup_box.get())
        try:
            await self.set_startup(on)
        except startup.StartupError as exc:
            log.warning("%s", exc)
            self.startup_box.deselect() if on else self.startup_box.select()
