"""Settings: look, TMDB, presets, the right-click menu, remembered matches, about."""

from __future__ import annotations

import asyncio
import logging
import os
import tkinter as tk
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from mediaforge import launch
from mediaforge.api.tmdb import TmdbClient, TmdbError
from mediaforge.api.updates import UpdateError
from mediaforge.core.formatter import render
from mediaforge.db.matches import RememberedMatch
from mediaforge.db.presets import LastPresetError
from mediaforge.logging_setup import set_secret
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia, TypeHint
from mediaforge.models.preset import Preset, PresetOptions
from mediaforge.platform import context_menu
from mediaforge.ui.queue_view import OPTION_LABELS
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.dialogs import ask_choice

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")

LANGUAGES = ["en-US", "en-GB", "nl-NL", "de-DE", "fr-FR", "es-ES", "it-IT", "pt-BR", "sv-SE"]
TMDB_KEY_PAGE = "https://www.themoviedb.org/settings/api"
_TYPE_LABELS = {TypeHint.AUTO: "Auto", TypeHint.MOVIE: "Movie", TypeHint.TV: "TV"}

_SAMPLE_EPISODE = ResolvedMedia(
    parsed=ParsedMedia(
        source_name="sample.mkv", kind=MediaKind.EPISODE, title="VisionQuest", season=1, episode=2
    ),
    title="VisionQuest",
    year=2026,
    episode_title="Theseus",
    match_state=MatchState.CONFIDENT,
)
_SAMPLE_MOVIE = ResolvedMedia(
    parsed=ParsedMedia(source_name="sample.mkv", kind=MediaKind.MOVIE, title="Dune Part Two"),
    title="Dune Part Two",
    year=2024,
    episode_title=None,
    match_state=MatchState.CONFIDENT,
)


def menu_icon() -> str:
    return context_menu.default_icon()


def menu_prefix() -> list[str]:
    return context_menu.command_prefix(launch.LAUNCHER)


class SettingsView(ctk.CTkFrame):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self.presets: list[Preset] = []
        self.editing: str | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.on_presets_changed: Callable[[], None] = lambda: None
        self.on_restart: Callable[[], None] = lambda: None  # MainShell.request_restart
        self.inform: Callable[[str, str], Awaitable[None]] = self._inform
        self._latest: str | None = None  # a newer version this copy can install
        self._updating = False

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(head, text="Settings", font=font(16, "bold"), text_color=palette.text).pack(
            side="left"
        )
        self.body = ctk.CTkScrollableFrame(self, fg_color=palette.main, corner_radius=0)
        self.body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_appearance()
        self._build_tmdb()
        self._build_presets()
        self._build_menu()
        self._build_matches()
        self._build_about()

    # --- plumbing --------------------------------------------------------------------------

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _inform(self, title: str, message: str) -> None:
        await ask_choice(self, self.palette, title, message, ["OK"])

    def _section(self, title: str, hint: str = "") -> ctk.CTkFrame:
        p = self.palette
        card = ctk.CTkFrame(self.body, fg_color=p.surface, corner_radius=8)
        card.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(card, text=title, font=font(14, "bold"), text_color=p.text).pack(
            anchor="w", padx=14, pady=(12, 0)
        )
        if hint:
            ctk.CTkLabel(
                card, text=hint, font=font(11), text_color=p.muted, justify="left", wraplength=700
            ).pack(anchor="w", padx=14)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(8, 12))
        return inner

    def _button(
        self,
        master: Any,
        text: str,
        command: Callable[[], Any],
        main: bool = False,
        width: int = 120,
    ) -> ctk.CTkButton:
        p = self.palette
        return ctk.CTkButton(
            master,
            text=text,
            width=width,
            height=28,
            command=command,
            fg_color=p.accent if main else "transparent",
            border_width=0 if main else 1,
            border_color=p.border,
            text_color=p.accent_text if main else p.text,
            hover_color=p.accent_hover if main else p.raised,
            font=font(12, "bold" if main else "normal"),
        )

    def set_kind(self, kind: str) -> None:
        """The Series | Movies switch: not used here (History mixes both; see the 1.2 plan)."""

    def on_show(self) -> None:
        self.spawn(self.reload())

    async def reload(self) -> None:
        settings = await self.services.settings.load()
        self.services.app_settings = settings
        self.theme.set(settings.theme)
        self.key_var.set(settings.tmdb_key)
        self.language.set(settings.tmdb_language)
        self.presets = await self.services.presets.list()
        if self.editing not in [p.name for p in self.presets]:
            self.editing = self.presets[0].name if self.presets else None
        self._fill_preset_list()
        self._load_editor()
        self._fill_menu_checks(settings.context_menu_presets)
        self.menu_label.configure(text=self.menu_status())
        if settings.auto_update:
            self.auto_switch.select()
        else:
            self.auto_switch.deselect()
        self.show_version()
        await self._fill_matches()

    # --- appearance ------------------------------------------------------------------------

    def _build_appearance(self) -> None:
        box = self._section("Appearance", "Changes apply the next time MediaForge starts.")
        self.theme = ctk.CTkSegmentedButton(
            box,
            values=["Dark", "Light", "System"],
            command=lambda value: self.spawn(self.set_theme(value)),
            selected_color=self.palette.accent,
            selected_hover_color=self.palette.accent_hover,
            unselected_color=self.palette.raised,
            text_color=self.palette.text,
            font=font(12),
        )
        self.theme.pack(anchor="w")

    async def set_theme(self, theme: str) -> None:
        settings = await self.services.settings.load()
        self.services.app_settings = settings.model_copy(update={"theme": theme})
        await self.services.settings.save(self.services.app_settings)
        log.info("Theme set to %s; restart MediaForge to see it", theme)

    # --- TMDB ------------------------------------------------------------------------------

    def _build_tmdb(self) -> None:
        p = self.palette
        box = self._section(
            "TMDB",
            "MediaForge looks up official titles, episode names and posters on The Movie "
            "Database. A free API key (v3 key or v4 read access token) is needed.",
        )
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x")
        self.key_var = tk.StringVar()
        self.key_entry = ctk.CTkEntry(
            row,
            textvariable=self.key_var,
            show="•",
            width=360,
            fg_color=p.main,
            border_color=p.border,
            text_color=p.text,
        )
        self.key_entry.pack(side="left")
        self.language = ctk.CTkComboBox(
            row,
            values=LANGUAGES,
            width=110,
            fg_color=p.main,
            border_color=p.border,
            button_color=p.raised,
            text_color=p.text,
        )
        self.language.pack(side="left", padx=8)
        self._button(
            row, "Check key", lambda: self.spawn(self.check_key(self.key_var.get())), width=100
        ).pack(side="left")
        self._button(
            row,
            "Save",
            lambda: self.spawn(self.save_tmdb(self.key_var.get(), self.language.get())),
            main=True,
            width=80,
        ).pack(side="left", padx=8)
        self.key_status = ctk.CTkLabel(box, text="", font=font(11), text_color=p.muted)
        self.key_status.pack(anchor="w", pady=(6, 0))
        link = ctk.CTkButton(
            box,
            text="Get a free key on themoviedb.org",
            fg_color="transparent",
            hover_color=p.raised,
            text_color=p.accent,
            font=font(11),
            width=10,
            command=lambda: os.startfile(TMDB_KEY_PAGE),
        )  # type: ignore[attr-defined]
        link.pack(anchor="w")

    async def check_key(self, key: str) -> bool:
        key = key.strip()
        if not key:
            self.key_status.configure(text="Enter a key first.", text_color=self.palette.muted)
            return False
        set_secret(key)
        try:
            ok = await TmdbClient(self.services.session, key).validate_key()
        except TmdbError as exc:
            self.key_status.configure(
                text=f"Couldn't reach TMDB: {exc}", text_color=self.palette.error
            )
            return False
        if ok:
            self.key_status.configure(text="✓ Key works", text_color=self.palette.success)
        else:
            self.key_status.configure(text="TMDB rejected this key", text_color=self.palette.error)
        return ok

    async def save_tmdb(self, key: str, language: str) -> None:
        from mediaforge.app import rebuild_tmdb

        settings = await self.services.settings.load()
        settings = settings.model_copy(
            update={"tmdb_key": key.strip(), "tmdb_language": language.strip() or "en-US"}
        )
        await self.services.settings.save(settings)
        await rebuild_tmdb(self.services)
        self.key_status.configure(text="Saved", text_color=self.palette.success)
        log.info("TMDB settings saved", extra={"success": True})

    # --- presets ---------------------------------------------------------------------------

    def _build_presets(self) -> None:
        p = self.palette
        box = self._section("Presets", "A preset is a media type, a naming format and extras.")
        box.grid_columnconfigure(1, weight=1)
        self.preset_list = ctk.CTkFrame(box, fg_color=p.main, corner_radius=6, width=200)
        self.preset_list.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        editor = ctk.CTkFrame(box, fg_color="transparent")
        editor.grid(row=0, column=1, sticky="new")
        editor.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(editor, text="Name", text_color=p.muted, font=font(11)).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.editor_name = tk.StringVar()
        ctk.CTkEntry(
            editor,
            textvariable=self.editor_name,
            fg_color=p.main,
            border_color=p.border,
            text_color=p.text,
        ).grid(row=0, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(editor, text="Type", text_color=p.muted, font=font(11)).grid(
            row=1, column=0, sticky="w"
        )
        self.editor_type = ctk.CTkSegmentedButton(
            editor,
            values=list(_TYPE_LABELS.values()),
            selected_color=p.accent,
            selected_hover_color=p.accent_hover,
            unselected_color=p.raised,
            text_color=p.text,
            command=lambda value: self._update_sample(),
        )
        self.editor_type.grid(row=1, column=1, sticky="w", pady=3)
        ctk.CTkLabel(editor, text="Format", text_color=p.muted, font=font(11)).grid(
            row=2, column=0, sticky="w"
        )
        self.editor_format = tk.StringVar()
        ctk.CTkEntry(
            editor,
            textvariable=self.editor_format,
            fg_color=p.main,
            border_color=p.border,
            text_color=p.text,
            font=font(12, mono=True),
        ).grid(row=2, column=1, sticky="ew", pady=3)
        self.editor_format.trace_add("write", lambda *_: self._update_sample())
        self.format_sample = ctk.CTkLabel(
            editor,
            text="",
            font=font(11, mono=True),
            text_color=p.muted,
            anchor="w",
            justify="left",
        )
        self.format_sample.grid(row=3, column=1, sticky="w")
        ctk.CTkLabel(
            editor,
            text="Codes: {ny} name (year) · {t} title · {s} season · {e} episode · "
            "{s00e00} S01E02 · {en} episode name · / makes a folder",
            font=font(10),
            text_color=p.muted,
            wraplength=560,
            justify="left",
        ).grid(row=4, column=1, sticky="w", pady=(0, 6))
        checks = ctk.CTkFrame(editor, fg_color="transparent")
        checks.grid(row=5, column=1, sticky="w")
        self.editor_options: dict[str, tk.BooleanVar] = {}
        for index, (key, label) in enumerate(OPTION_LABELS.items()):
            var = tk.BooleanVar()
            self.editor_options[key] = var
            ctk.CTkCheckBox(
                checks,
                text=label,
                variable=var,
                fg_color=p.accent,
                hover_color=p.accent_hover,
                border_color=p.border,
                text_color=p.text,
                font=font(11),
                checkbox_width=16,
                checkbox_height=16,
            ).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 16), pady=2)
        buttons = ctk.CTkFrame(editor, fg_color="transparent")
        buttons.grid(row=6, column=1, sticky="w", pady=(10, 0))
        self._button(
            buttons,
            "Save preset",
            lambda: self.spawn(self.save_preset(self._editor_preset(), self.editing)),
            main=True,
        ).pack(side="left")
        self._button(buttons, "New", self._new_preset, width=70).pack(side="left", padx=8)
        self._button(
            buttons, "Delete", lambda: self.spawn(self.delete_preset(self.editing or "")), width=80
        ).pack(side="left")

    def _fill_preset_list(self) -> None:
        p = self.palette
        for child in self.preset_list.winfo_children():
            child.destroy()
        for preset in self.presets:
            active = preset.name == self.editing
            ctk.CTkButton(
                self.preset_list,
                text=preset.name,
                anchor="w",
                height=28,
                width=190,
                fg_color=p.raised if active else "transparent",
                hover_color=p.raised,
                text_color=p.accent if active else p.text,
                font=font(12),
                command=lambda n=preset.name: self._edit(n),
            ).pack(fill="x", padx=4, pady=2)

    def _edit(self, name: str) -> None:
        self.editing = name
        self._fill_preset_list()
        self._load_editor()

    def _new_preset(self) -> None:
        self.editing = None
        self.editor_name.set("New preset")
        self.editor_type.set("Auto")
        self.editor_format.set("{ny}")
        for var in self.editor_options.values():
            var.set(False)
        self._fill_preset_list()

    def _load_editor(self) -> None:
        preset = next((p for p in self.presets if p.name == self.editing), None)
        if preset is None:
            return
        self.editor_name.set(preset.name)
        self.editor_type.set(_TYPE_LABELS[preset.type])
        self.editor_format.set(preset.format)
        for key, var in self.editor_options.items():
            var.set(bool(getattr(preset.options, key)))
        self._update_sample()

    def _editor_preset(self) -> Preset:
        label = self.editor_type.get()
        type_hint = next((t for t, name in _TYPE_LABELS.items() if name == label), TypeHint.AUTO)
        options = PresetOptions(**{k: v.get() for k, v in self.editor_options.items()})
        return Preset(
            name=self.editor_name.get().strip(),
            type=type_hint,
            format=self.editor_format.get().strip() or "{ny}",
            options=options,
        )

    def _update_sample(self) -> None:
        sample = _SAMPLE_MOVIE if self.editor_type.get() == "Movie" else _SAMPLE_EPISODE
        rendered = render(self.editor_format.get(), sample, ".mkv")
        text = "↳ " + "\\".join(rendered.parts)
        if rendered.unknown:
            text += "   ⚠ not a known code: " + ", ".join("{" + u + "}" for u in rendered.unknown)
        self.format_sample.configure(text=text)

    async def save_preset(self, preset: Preset, old_name: str | None) -> None:
        if not preset.name:
            await self.inform("Name needed", "Give the preset a name first.")
            return
        await self.services.presets.save(preset, old_name=old_name)
        self.editing = preset.name
        await self.reload()
        self.on_presets_changed()
        log.info("Preset '%s' saved", preset.name, extra={"success": True})

    async def delete_preset(self, name: str) -> None:
        if not name:
            return
        try:
            await self.services.presets.delete(name)
        except LastPresetError:
            await self.inform("Can't delete", "The last preset can't be deleted.")
            return
        self.editing = None
        await self.reload()
        self.on_presets_changed()

    # --- right-click menu ------------------------------------------------------------------

    def _build_menu(self) -> None:
        p = self.palette
        box = self._section(
            "Right-click menu",
            "Adds a MediaForge submenu to Explorer's right-click menu for files and folders "
            "(on Windows 11 it's under 'Show more options'). No administrator rights needed.",
        )
        self.menu_checks_frame = ctk.CTkFrame(box, fg_color="transparent")
        self.menu_checks_frame.pack(fill="x")
        self.menu_checks: dict[str, tk.BooleanVar] = {}
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self._button(
            row,
            "Add to right-click menu",
            lambda: self.spawn(self.install_menu(self._checked_presets())),
            main=True,
            width=190,
        ).pack(side="left")
        self._button(row, "Remove", lambda: self.spawn(self.remove_menu()), width=90).pack(
            side="left", padx=8
        )
        self._button(row, "Save as .reg file…", self._save_reg, width=150).pack(side="left")
        self._button(
            row, "Save removal file for the old menu…", self._save_legacy_removal, width=260
        ).pack(side="left", padx=8)
        self.menu_label = ctk.CTkLabel(
            box, text="", font=font(11), text_color=p.muted, justify="left", wraplength=700
        )
        self.menu_label.pack(anchor="w", pady=(6, 0))

    def _fill_menu_checks(self, chosen: Sequence[str]) -> None:
        p = self.palette
        for child in self.menu_checks_frame.winfo_children():
            child.destroy()
        self.menu_checks.clear()
        for index, preset in enumerate(self.presets):
            var = tk.BooleanVar(value=(preset.name in chosen) if chosen else True)
            self.menu_checks[preset.name] = var
            ctk.CTkCheckBox(
                self.menu_checks_frame,
                text=preset.name,
                variable=var,
                fg_color=p.accent,
                hover_color=p.accent_hover,
                border_color=p.border,
                text_color=p.text,
                font=font(11),
                checkbox_width=16,
                checkbox_height=16,
            ).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 18), pady=2)

    def _checked_presets(self) -> list[str]:
        return [name for name, var in self.menu_checks.items() if var.get()]

    def menu_status(self) -> str:
        lines: list[str] = []
        current = context_menu.installed_command()
        prefix = " ".join(f'"{part}"' for part in menu_prefix())
        if current is None:
            lines.append("Not in the right-click menu yet.")
        elif not current.startswith(prefix):
            lines.append(
                "The menu points to an old location of MediaForge. Click 'Add to "
                "right-click menu' to fix it."
            )
        else:
            lines.append("✓ MediaForge is in the right-click menu.")
        if context_menu.legacy_installed():
            lines.append(
                "Old MediaForge menu found. Save the removal file and double-click it "
                "(Windows asks for administrator approval) to remove the old entries."
            )
        return "\n".join(lines)

    async def install_menu(self, names: Sequence[str]) -> None:
        if not names:
            await self.inform("Pick presets", "Tick at least one preset for the menu.")
            return
        entries = context_menu.build_entries(names, menu_prefix(), menu_icon())
        await asyncio.to_thread(context_menu.install, entries)
        settings = await self.services.settings.load()
        every = set(names) == {p.name for p in self.presets}
        self.services.app_settings = settings.model_copy(
            update={"context_menu_presets": [] if every else list(names)}  # [] = all, later too
        )
        await self.services.settings.save(self.services.app_settings)
        self.menu_label.configure(text=self.menu_status())
        log.info("Right-click menu updated", extra={"success": True})

    async def remove_menu(self) -> None:
        await asyncio.to_thread(context_menu.uninstall)
        self.menu_label.configure(text=self.menu_status())
        log.info("Right-click menu removed")

    def _save_reg(self) -> None:
        names = self._checked_presets()
        path = filedialog.asksaveasfilename(
            title="Save .reg file",
            defaultextension=".reg",
            initialfile="MediaForge menu.reg",
            filetypes=[("Registry file", "*.reg")],
        )
        if path:
            entries = context_menu.build_entries(names, menu_prefix(), menu_icon())
            Path(path).write_text(context_menu.to_reg_file(entries), encoding="utf-16")

    def _save_legacy_removal(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save removal file",
            defaultextension=".reg",
            initialfile="Remove old MediaForge menu.reg",
            filetypes=[("Registry file", "*.reg")],
        )
        if path:
            Path(path).write_text(context_menu.legacy_removal_reg(), encoding="utf-16")

    # --- remembered matches ----------------------------------------------------------------

    def _build_matches(self) -> None:
        box = self._section(
            "Remembered matches",
            "Your picks for uncertain TMDB matches. Forget one to be asked again.",
        )
        self.matches_frame = ctk.CTkFrame(box, fg_color="transparent")
        self.matches_frame.pack(fill="x")
        self._button(box, "Clear all", lambda: self.spawn(self.clear_matches()), width=90).pack(
            anchor="w", pady=(6, 0)
        )

    async def _fill_matches(self) -> None:
        p = self.palette
        for child in self.matches_frame.winfo_children():
            child.destroy()
        matches = await self.services.matches.list()
        if not matches:
            ctk.CTkLabel(
                self.matches_frame, text="None yet.", font=font(11), text_color=p.muted
            ).pack(anchor="w")
        for match in matches:
            row = ctk.CTkFrame(self.matches_frame, fg_color="transparent")
            row.pack(fill="x")
            year = f" ({match.year})" if match.year else ""
            kind = "show" if match.kind is MediaKind.EPISODE else "movie"
            ctk.CTkLabel(
                row,
                text=f"{match.norm_title}{year} → {match.label}  ·  {kind}",
                font=font(11),
                text_color=p.text,
            ).pack(side="left")
            self._button(
                row, "Forget", lambda m=match: self.spawn(self.forget_match(m)), width=70
            ).pack(side="right", pady=2)

    async def forget_match(self, match: RememberedMatch) -> None:
        await self.services.matches.forget(match.kind, match.norm_title, match.year)
        await self._fill_matches()

    async def clear_matches(self) -> None:
        await self.services.matches.clear()
        await self._fill_matches()

    # --- about -----------------------------------------------------------------------------

    def _build_about(self) -> None:
        box = self._section("About")
        p, paths = self.palette, self.services.paths
        self.version_label = ctk.CTkLabel(box, text="", font=font(12, "bold"), text_color=p.text)
        self.version_label.pack(anchor="w")
        ctk.CTkLabel(
            box,
            text=f"Settings and history: {paths.data_dir}",
            font=font(11),
            text_color=p.muted,
        ).pack(anchor="w")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self.update_button = self._button(
            row, "Check for updates", lambda: self.spawn(self._update_clicked()), width=160
        )
        self.update_button.pack(side="left")
        self.update_label = ctk.CTkLabel(
            row, text="", font=font(11), text_color=p.muted, justify="left", wraplength=560
        )
        self.update_label.pack(side="left", padx=10)
        self.auto_switch = ctk.CTkSwitch(
            box,
            text="Download and install updates automatically (when MediaForge closes)",
            font=font(11),
            text_color=p.text,
            progress_color=p.accent,
            command=lambda: self.spawn(self.set_auto_update(bool(self.auto_switch.get()))),
        )
        self.auto_switch.pack(anchor="w", pady=(8, 0))
        self._button(
            box,
            "Open log folder",
            lambda: os.startfile(paths.logs_dir),  # type: ignore[attr-defined]
            width=130,
        ).pack(anchor="w", pady=(8, 0))
        self.show_version()

    def show_version(self) -> None:
        self.version_label.configure(text=f"MediaForge {self.services.updates.current}")

    async def _update_clicked(self) -> None:
        if self._latest is not None:
            await self.update_now()
        else:
            await self.check_for_updates()

    async def check_for_updates(self) -> None:
        self._latest = None
        self.update_button.configure(text="Check for updates")
        self.update_label.configure(text="Checking GitHub…")
        try:
            info = await self.services.updates.check()
        except UpdateError as exc:
            self.update_label.configure(text=str(exc))
            return
        if not info.available:
            text = f"MediaForge {info.current} is up to date."
        elif not info.installable:
            text = (
                f"MediaForge {info.latest} is on GitHub. This is a development copy, "
                "so it doesn't update itself."
            )
        else:
            self._latest = info.latest
            self.update_button.configure(text=f"Update to {info.latest}")
            text = "A new version is ready. MediaForge restarts to install it."
        self.update_label.configure(text=text)

    async def update_now(self) -> None:
        if self._updating or self._latest is None:
            return
        self._updating = True
        self.update_button.configure(state="disabled")
        self.update_label.configure(text="Downloading and installing…")
        try:
            version = await self.services.updates.install()
        except UpdateError as exc:
            self.update_label.configure(text=str(exc))
            self.update_button.configure(state="normal")
            self._updating = False
            return
        self.update_label.configure(text=f"MediaForge {version} is installed. Restarting…")
        log.info("Installed MediaForge %s; restarting", version, extra={"success": True})
        self.on_restart()

    async def set_auto_update(self, value: bool) -> None:
        settings = await self.services.settings.load()
        self.services.app_settings = settings.model_copy(update={"auto_update": value})
        await self.services.settings.save(self.services.app_settings)

    def destroy(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        super().destroy()
