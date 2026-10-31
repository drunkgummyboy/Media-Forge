"""The right-hand panel: poster, official title, episode, match status, destination."""

from __future__ import annotations

import asyncio
import io
import logging
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk
from PIL import Image

from mediaforge.api.trailers import youtube_search_url
from mediaforge.core.queue import QueueItem
from mediaforge.models.media import MatchState, MediaKind
from mediaforge.ui.theme import Palette, font

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")

POSTER_W, POSTER_H = 216, 320
_BLANK: list[ctk.CTkImage] = []


def _blank_image() -> ctk.CTkImage:
    if not _BLANK:
        pixel = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        _BLANK.append(ctk.CTkImage(light_image=pixel, dark_image=pixel, size=(1, 1)))
    return _BLANK[0]


_MATCH_TEXT = {
    MatchState.CONFIDENT: "Matched on TMDB",
    MatchState.REMEMBERED: "Matched on TMDB (your pick)",
    MatchState.UNCERTAIN: "Not sure which one this is",
    MatchState.NOT_FOUND: "No TMDB match, using the file name",
    MatchState.SKIPPED: "TMDB skipped for this title",
    MatchState.OFFLINE: "Using the file name (TMDB not available)",
}


class DetailPanel(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        services: Services,
        palette: Palette,
        on_change_match: Callable[[QueueItem], None],
    ) -> None:
        super().__init__(master, width=260, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self._on_change_match = on_change_match
        self._item: QueueItem | None = None
        self._token = 0
        self._image: ctk.CTkImage | None = None
        self._task: asyncio.Task[None] | None = None
        p = palette

        self.poster = ctk.CTkLabel(
            self,
            text="",
            width=POSTER_W,
            height=POSTER_H,
            fg_color=p.surface,
            corner_radius=8,
            text_color=p.muted,
            font=font(11),
        )
        self.poster.pack(padx=18, pady=(18, 10))
        self.title = ctk.CTkLabel(
            self,
            text="",
            font=font(15, "bold"),
            text_color=p.text,
            wraplength=220,
            justify="left",
            anchor="w",
        )
        self.title.pack(fill="x", padx=18)
        self.episode = ctk.CTkLabel(
            self,
            text="",
            font=font(12),
            text_color=p.muted,
            wraplength=220,
            justify="left",
            anchor="w",
        )
        self.episode.pack(fill="x", padx=18, pady=(2, 6))
        match_row = ctk.CTkFrame(self, fg_color="transparent")
        match_row.pack(fill="x", padx=18)
        self.match = ctk.CTkLabel(
            match_row,
            text="",
            font=font(11),
            text_color=p.muted,
            wraplength=220,
            justify="left",
            anchor="w",
        )
        self.match.pack(fill="x")
        self.change = ctk.CTkButton(
            match_row,
            text="Change match…",
            width=10,
            height=22,
            fg_color="transparent",
            hover_color=p.raised,
            text_color=p.accent,
            font=font(11),
            anchor="w",
            command=self._change_clicked,
        )
        self.change.pack(anchor="w", pady=(2, 8))
        self.destination = ctk.CTkLabel(
            self,
            text="",
            font=font(10, mono=True),
            text_color=p.text,
            fg_color=p.surface,
            corner_radius=6,
            wraplength=210,
            justify="left",
            anchor="w",
        )
        self.destination.pack(fill="x", padx=18, ipadx=8, ipady=8)
        self.original = ctk.CTkLabel(
            self,
            text="",
            font=font(10),
            text_color=p.muted,
            wraplength=220,
            justify="left",
            anchor="w",
        )
        self.original.pack(fill="x", padx=18, pady=(8, 4))
        self.trailer = ctk.CTkButton(
            self,
            text="▶  Watch trailer",
            height=28,
            fg_color="transparent",
            border_width=1,
            border_color=p.accent,
            text_color=p.accent,
            hover_color=p.raised,
            font=font(11),
            command=self._watch_trailer,
        )
        self.trailer.pack(anchor="w", padx=18, pady=(6, 0))
        self.show(None)

    def show(self, item: QueueItem | None, target: Path | None = None) -> None:
        self._item = item
        self._token += 1
        widgets = (self.title, self.episode, self.match, self.destination, self.original)
        if item is None:
            for w in widgets:
                w.configure(text="")
            self.change.pack_forget()
            self.trailer.pack_forget()
            self._set_poster(None, "Select a file to see\nits details")
            return
        media = item.media
        self.title.configure(text=media.ny)
        episode = ""
        if media.kind is MediaKind.EPISODE and media.season is not None:
            code = f"S{media.season:02d}E{media.episode or 0:02d}"
            if media.episode_end:
                code += f"-E{media.episode_end:02d}"
            episode = f"{code} · {media.episode_title}" if media.episode_title else code
        self.episode.configure(text=episode)
        if item.resolution is None:
            self.match.configure(text="Looking it up on TMDB…")
        else:
            text = _MATCH_TEXT[media.match_state]
            if not self.services.tmdb and media.match_state is MatchState.OFFLINE:
                text = "No TMDB key: using the file name"
            self.match.configure(text=text)
        if self.services.tmdb:
            self.change.pack(anchor="w", pady=(2, 8))
        else:
            self.change.pack_forget()
        self.destination.configure(text=str(target) if target else "")
        self.original.configure(text=f"Now: {item.path.name}")
        self.trailer.pack(anchor="w", padx=18, pady=(6, 0))
        self._set_poster(None, "No poster" if not media.poster_path else "Loading poster…")
        if media.poster_path and self.services.tmdb:
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = asyncio.get_running_loop().create_task(
                self._load_poster(media.poster_path, self._token)
            )

    def _set_poster(self, image: ctk.CTkImage | None, text: str = "") -> None:
        # CTkLabel ignores image=None and keeps showing the old Tk image, which breaks once
        # that image is freed. A transparent 1-pixel image replaces it instead.
        self.poster.configure(image=image or _blank_image(), text="" if image else text)
        self._image = image

    async def _load_poster(self, path: str, token: int) -> None:
        tmdb = self.services.tmdb
        if tmdb is None:
            return
        try:
            data = await tmdb.image(path, "w342")
        except Exception as exc:
            log.debug("Poster %s unavailable: %s", path, exc)
            if token == self._token:
                self._set_poster(None, "No poster")
            return
        if token != self._token or not self.winfo_exists():
            return
        picture = Image.open(io.BytesIO(data))
        height = min(POSTER_H, round(POSTER_W * picture.height / max(picture.width, 1)))
        self._set_poster(
            ctk.CTkImage(light_image=picture, dark_image=picture, size=(POSTER_W, height))
        )

    def _change_clicked(self) -> None:
        if self._item is not None:
            self._on_change_match(self._item)

    def _watch_trailer(self) -> None:
        if self._item is not None:
            media = self._item.media
            webbrowser.open(youtube_search_url(media.title, media.year))

    def destroy(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        super().destroy()
