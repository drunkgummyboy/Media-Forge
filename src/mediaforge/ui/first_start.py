"""The welcome window on first start: old settings, TMDB key, right-click menu."""

from __future__ import annotations

import asyncio
import logging
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from mediaforge.api.tmdb import TmdbClient, TmdbError
from mediaforge.db.legacy_import import UNDO_NAME, import_legacy
from mediaforge.logging_setup import set_secret
from mediaforge.platform import context_menu
from mediaforge.ui.settings_view import TMDB_KEY_PAGE, menu_icon, menu_prefix
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.popups import prepare_popup

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")


class FirstStart(ctk.CTkToplevel):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master)
        self.services = services
        self.palette = palette
        self.done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        p = palette
        settings = services.app_settings

        self.title("Welcome to MediaForge")
        self.geometry("620x560")
        self.configure(fg_color=p.main)
        prepare_popup(self)
        self.protocol(
            "WM_DELETE_WINDOW", lambda: asyncio.get_running_loop().create_task(self.finish())
        )

        ctk.CTkLabel(
            self, text="Welcome to MediaForge", font=font(18, "bold"), text_color=p.text
        ).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(
            self,
            text="Three quick things, then you're ready. You can change all of "
            "them later in Settings.",
            font=font(12),
            text_color=p.muted,
        ).pack(anchor="w", padx=24, pady=(0, 12))

        step1 = self._step("1 · Settings from the old MediaForge")
        self.import_status = ctk.CTkLabel(
            step1, text="", font=font(11), text_color=p.muted, justify="left", wraplength=540
        )
        self.import_status.pack(anchor="w")
        ctk.CTkButton(
            step1,
            text="Import from mediaforge_config.json…",
            height=28,
            fg_color="transparent",
            border_width=1,
            border_color=p.border,
            text_color=p.text,
            hover_color=p.raised,
            font=font(12),
            command=self._pick_config,
        ).pack(anchor="w", pady=(6, 0))
        if settings.legacy_imported and settings.tmdb_key:
            self.import_status.configure(text="✓ Your old presets and TMDB key were imported.")
        else:
            self.import_status.configure(
                text="Used MediaForge before? Import your presets and "
                "TMDB key from the old settings file."
            )

        step2 = self._step("2 · TMDB key")
        ctk.CTkLabel(
            step2,
            text="Needed for official titles, episode names and posters. "
            "MediaForge still works without it, using the file names.",
            font=font(11),
            text_color=p.muted,
            wraplength=540,
            justify="left",
        ).pack(anchor="w")
        row = ctk.CTkFrame(step2, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.key_var = tk.StringVar(value=settings.tmdb_key)
        ctk.CTkEntry(
            row,
            textvariable=self.key_var,
            show="•",
            width=330,
            fg_color=p.surface,
            border_color=p.border,
            text_color=p.text,
        ).pack(side="left")
        ctk.CTkButton(
            row,
            text="Check key",
            width=90,
            height=28,
            fg_color="transparent",
            border_width=1,
            border_color=p.border,
            text_color=p.text,
            hover_color=p.raised,
            command=lambda: asyncio.get_running_loop().create_task(self.check_key()),
        ).pack(side="left", padx=8)
        self.key_status = ctk.CTkLabel(step2, text="", font=font(11), text_color=p.muted)
        self.key_status.pack(anchor="w")
        ctk.CTkButton(
            step2,
            text="Get a free key on themoviedb.org",
            width=10,
            fg_color="transparent",
            hover_color=p.raised,
            text_color=p.accent,
            font=font(11),
            command=lambda: os.startfile(TMDB_KEY_PAGE),  # type: ignore[attr-defined]
        ).pack(anchor="w")

        step3 = self._step("3 · Right-click menu")
        self.menu_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            step3,
            text="Add MediaForge to Explorer's right-click menu",
            variable=self.menu_var,
            fg_color=p.accent,
            hover_color=p.accent_hover,
            border_color=p.border,
            text_color=p.text,
            font=font(12),
        ).pack(anchor="w")

        ctk.CTkButton(
            self,
            text="Start using MediaForge",
            height=34,
            fg_color=p.accent,
            text_color=p.accent_text,
            hover_color=p.accent_hover,
            font=font(13, "bold"),
            command=lambda: asyncio.get_running_loop().create_task(self.finish()),
        ).pack(anchor="e", padx=24, pady=18)

    def _step(self, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self, fg_color=self.palette.surface, corner_radius=8)
        card.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(card, text=title, font=font(13, "bold"), text_color=self.palette.text).pack(
            anchor="w", padx=14, pady=(10, 2)
        )
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(0, 10))
        return inner

    def _pick_config(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Find mediaforge_config.json",
            filetypes=[("MediaForge settings", "mediaforge_config.json"), ("JSON", "*.json")],
        )
        if path:
            asyncio.get_running_loop().create_task(self.import_from(Path(path)))

    async def import_from(self, config: Path) -> None:
        s = self.services
        undo = config.with_name(UNDO_NAME)
        report = await import_legacy(
            config, undo if undo.exists() else None, s.settings, s.presets, s.history, force=True
        )
        s.app_settings = await s.settings.load()
        if s.app_settings.tmdb_key:
            self.key_var.set(s.app_settings.tmdb_key)
        self.import_status.configure(
            text=f"✓ Imported {report.presets} preset(s)"
            + (" and your TMDB key" if report.key_imported else "")
            + (
                f", plus {report.history_files} past renames to History"
                if report.history_files
                else ""
            )
            + "."
        )

    async def check_key(self) -> bool:
        key = self.key_var.get().strip()
        if not key:
            return False
        set_secret(key)
        try:
            ok = await TmdbClient(self.services.session, key).validate_key()
        except TmdbError as exc:
            self.key_status.configure(
                text=f"Couldn't reach TMDB: {exc}", text_color=self.palette.error
            )
            return False
        self.key_status.configure(
            text="✓ Key works" if ok else "TMDB rejected this key",
            text_color=self.palette.success if ok else self.palette.error,
        )
        return ok

    async def finish(self) -> None:
        from mediaforge.app import rebuild_tmdb

        s = self.services
        settings = await s.settings.load()
        settings = settings.model_copy(
            update={"tmdb_key": self.key_var.get().strip(), "first_start_done": True}
        )
        if self.menu_var.get():
            names = [p.name for p in await s.presets.list()]
            entries = context_menu.build_entries(names, menu_prefix(), menu_icon())
            try:
                await asyncio.to_thread(context_menu.install, entries)
                settings = settings.model_copy(update={"context_menu_presets": []})  # all
            except OSError as exc:
                log.error("Couldn't add the right-click menu: %s", exc)
        await s.settings.save(settings)
        await rebuild_tmdb(s)
        if not self.done.done():
            self.done.set_result(None)
        self.destroy()

    @classmethod
    async def run(cls, master: Any, services: Services, palette: Palette) -> None:
        window = cls(master, services, palette)
        window.after(50, window.grab_set)
        await window.done
