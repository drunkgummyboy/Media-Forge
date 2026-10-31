"""Pick the right TMDB entry when MediaForge isn't sure (thumbnails and summaries)."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING, Any, Literal

import customtkinter as ctk
from PIL import Image

from mediaforge.core.matcher import MAX_CANDIDATES
from mediaforge.core.queue import QueueItem
from mediaforge.models.media import MediaKind
from mediaforge.models.tmdb import TmdbResult
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.popups import prepare_popup

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")

type PickResult = TmdbResult | None | Literal["cancel"]


class MatchPicker(ctk.CTkToplevel):
    def __init__(self, master: Any, item: QueueItem, services: Services, palette: Palette) -> None:
        super().__init__(master)
        self.services = services
        self.palette = palette
        self.item = item
        self.result: asyncio.Future[PickResult] = asyncio.get_running_loop().create_future()
        self._images: list[ctk.CTkImage] = []
        self._tasks: list[asyncio.Task[None]] = []
        kind = "show" if item.parsed.kind is MediaKind.EPISODE else "movie"

        self.title("Choose the right match")
        self.geometry("560x620")
        self.configure(fg_color=palette.main)
        prepare_popup(self)
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish("cancel"))
        ctk.CTkLabel(
            self, text=f"Which {kind} is this?", font=font(16, "bold"), text_color=palette.text
        ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            self,
            text=item.path.name,
            font=font(11, mono=True),
            text_color=palette.muted,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=20)
        ctk.CTkLabel(
            self,
            text="Your choice is remembered for this title.",
            font=font(11),
            text_color=palette.muted,
        ).pack(anchor="w", padx=20, pady=(2, 10))
        self.list = ctk.CTkScrollableFrame(self, fg_color=palette.surface, corner_radius=8)
        self.list.pack(fill="both", expand=True, padx=20)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=14)
        self.top_button = ctk.CTkButton(
            footer,
            text="Use top result",
            fg_color="transparent",
            border_width=1,
            border_color=palette.border,
            text_color=palette.text,
            hover_color=palette.raised,
            command=self._use_top,
        )
        self.top_button.pack(side="right")
        ctk.CTkButton(
            footer,
            text="Skip TMDB for this file",
            fg_color="transparent",
            border_width=1,
            border_color=palette.border,
            text_color=palette.text,
            hover_color=palette.raised,
            command=lambda: self._finish(None),
        ).pack(side="right", padx=(0, 8))
        self.candidates: list[TmdbResult] = []

    async def load(self) -> None:
        candidates = list(self.item.resolution.candidates) if self.item.resolution else []
        tmdb = self.services.tmdb
        if not candidates and tmdb is not None:
            parsed = self.item.parsed
            try:
                if parsed.kind is MediaKind.EPISODE:
                    candidates = await tmdb.search_tv(parsed.title, parsed.year)
                else:
                    candidates = await tmdb.search_movie(parsed.title, parsed.year)
            except Exception as exc:
                log.warning("TMDB search failed: %s", exc)
        self.candidates = candidates[:MAX_CANDIDATES]
        if not self.candidates:
            ctk.CTkLabel(
                self.list, text="TMDB found nothing for this file.", text_color=self.palette.muted
            ).pack(pady=20)
            self.top_button.configure(state="disabled")
        for result in self.candidates:
            self._add_row(result)

    def _add_row(self, result: TmdbResult) -> None:
        p = self.palette
        row = ctk.CTkFrame(self.list, fg_color=p.main, corner_radius=6)
        row.pack(fill="x", padx=4, pady=4)
        row.grid_columnconfigure(1, weight=1)
        thumb = ctk.CTkLabel(
            row, text="🎬", width=50, height=75, fg_color=p.raised, corner_radius=4
        )
        thumb.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        year = result.year or "—"
        ctk.CTkLabel(
            row,
            text=f"{result.title} ({year})",
            font=font(13, "bold"),
            text_color=p.text,
            anchor="w",
            justify="left",
            wraplength=320,
        ).grid(row=0, column=1, sticky="w", pady=(8, 0))
        overview = result.overview.strip()
        if len(overview) > 160:
            overview = overview[:157] + "…"
        ctk.CTkLabel(
            row,
            text=overview or "No description.",
            font=font(10),
            text_color=p.muted,
            anchor="w",
            justify="left",
            wraplength=320,
        ).grid(row=1, column=1, sticky="w", pady=(0, 8))
        ctk.CTkButton(
            row,
            text="Use this",
            width=84,
            fg_color=p.accent,
            text_color=p.accent_text,
            hover_color=p.warning,
            command=lambda r=result: self._finish(r),
        ).grid(row=0, column=2, rowspan=2, padx=8)
        if result.poster_path and self.services.tmdb:
            task = asyncio.get_running_loop().create_task(self._thumb(thumb, result.poster_path))
            self._tasks.append(task)

    async def _thumb(self, label: ctk.CTkLabel, path: str) -> None:
        tmdb = self.services.tmdb
        if tmdb is None:
            return
        try:
            data = await tmdb.image(path, "w92")
        except Exception:
            return
        if not label.winfo_exists():
            return
        picture = Image.open(io.BytesIO(data))
        image = ctk.CTkImage(light_image=picture, dark_image=picture, size=(50, 75))
        self._images.append(image)
        label.configure(image=image, text="")

    def _use_top(self) -> None:
        if self.candidates:
            self._finish(self.candidates[0])

    def _finish(self, value: PickResult) -> None:
        if not self.result.done():
            self.result.set_result(value)
        for task in self._tasks:
            task.cancel()
        self.destroy()

    @classmethod
    async def ask(
        cls, master: Any, item: QueueItem, services: Services, palette: Palette
    ) -> PickResult:
        picker = cls(master, item, services, palette)
        await picker.load()
        picker.after(10, picker.grab_set)
        return await picker.result
