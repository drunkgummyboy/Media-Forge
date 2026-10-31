"""A fast list for thousands of rows: only the rows on screen are drawn, on a canvas."""

from __future__ import annotations

import bisect
import tkinter as tk
import tkinter.font as tkfont
import zlib
from collections.abc import Callable, Sequence
from typing import Any, Literal

import customtkinter as ctk
from pydantic import BaseModel

from mediaforge.ui.theme import FONT_FAMILY, MONO_FAMILY, Palette

THUMB_COLOURS = ("#5b3fa8", "#a8742b", "#1f8a55", "#b05a26", "#2f6f9a", "#8a3f6b", "#4d6b2f")


class Row(BaseModel):
    key: int
    # group: a quiet heading; section: an upper-case heading ("NEEDS YOU · 1"); band: a show's
    # or movie's band (backdrop, poster, title, buttons); item: a file line.
    kind: Literal["group", "item", "section", "band"]
    title: str
    subtitle: str = ""
    badge: str = ""
    status: str = ""
    highlight: tuple[int, int] | None = None  # the SxxEyy part of the title
    checked: bool | None = None  # None: no tick box
    check_disabled: bool = False
    art: str | None = None  # a band's poster (TMDB path)
    backdrop: str | None = None  # a band's backdrop (TMDB path)
    actions: tuple[tuple[str, str], ...] = ()  # a band's buttons: (id, label), the last filled
    note: str = ""  # a band's quiet text on the right (a time)
    code: str = ""  # an episode line's code ("S04E03"), in the accent colour
    faint: str = ""  # a line's old file name, faint on the right


def _fit(text: str, font: tkfont.Font, width: int) -> str:
    """Cut text with an ellipsis so it fits in width pixels."""
    if width <= 0:
        return ""
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class VirtualList(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        palette: Palette,
        row_height: int = 46,
        group_height: int = 26,
        *,
        removable: bool = True,
        thumbnails: bool = True,
        band_height: int = 64,
        section_height: int = 30,
    ) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.palette = palette
        self.on_select: Callable[[int | None], None] = lambda key: None
        self.on_remove: Callable[[int], None] = lambda key: None
        self.on_toggle: Callable[[int, bool], None] = lambda key, value: None
        self.on_action: Callable[[int, str], None] = lambda key, action: None
        # A band's (poster, backdrop) Tk images for its size, or None while not (yet) loaded.
        self.images: Callable[[Row, int, int], tuple[Any, Any]] = lambda row, w, h: (None, None)
        self._hits: list[tuple[int, int, int, int, int, str]] = []  # buttons, canvas coordinates
        self.removable = removable
        self.thumbnails = thumbnails
        self.selected: int | None = None
        self._rows: list[Row] = []
        self._tops: list[int] = []
        self._total = 0
        self._visible: list[int] = []
        self._hover: int | None = None
        self._redraw_pending = False

        scale = self._get_widget_scaling()
        self._row_h = round(row_height * scale)
        self._group_h = round(group_height * scale)
        self._band_h = round(band_height * scale)
        self._section_h = round(section_height * scale)
        self._pad = round(8 * scale)
        self._box = round(16 * scale)
        size = lambda px: -round(px * scale)  # noqa: E731 - negative = pixels
        self._title_font = tkfont.Font(family=FONT_FAMILY, size=size(12), weight="bold")
        self._sub_font = tkfont.Font(family=MONO_FAMILY, size=size(10))
        self._group_font = tkfont.Font(family=FONT_FAMILY, size=size(10), weight="bold")
        self._status_font = tkfont.Font(family=FONT_FAMILY, size=size(13))
        self._thumb_font = tkfont.Font(family=FONT_FAMILY, size=size(9), weight="bold")
        self._band_font = tkfont.Font(family=FONT_FAMILY, size=size(14), weight="bold")
        self._band_sub_font = tkfont.Font(family=FONT_FAMILY, size=size(11))
        self._button_font = tkfont.Font(family=FONT_FAMILY, size=size(11))
        self._line_font = tkfont.Font(family=FONT_FAMILY, size=size(12))
        self._code_font = tkfont.Font(family=FONT_FAMILY, size=size(12), weight="bold")
        # Episode lines start where a band's text starts: under the title, past the poster.
        poster_h = self._band_h - 6 - 2 * round(6 * scale)
        self._line_x = (
            self._pad // 2 + round(8 * scale) + round(poster_h * 2 / 3) + round(12 * scale)
        )
        self._status_style: dict[str, tuple[str, str]] = {
            "looking_up": ("…", palette.muted),
            "ready": ("●", palette.success),
            "needs_choice": ("?", palette.accent),
            "working": ("⟳", palette.accent),
            "done": ("✓", palette.success),
            "error": ("!", palette.error),
            "skipped": ("–", palette.muted),
        }

        self.canvas = tk.Canvas(
            self, bg=palette.main, highlightthickness=0, bd=0, yscrollincrement=1, takefocus=1
        )
        self.scrollbar = ctk.CTkScrollbar(self, command=self._on_scrollbar)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))

    # --- data ------------------------------------------------------------------------------

    def set_rows(self, rows: Sequence[Row]) -> None:
        self._rows = list(rows)
        self._tops = []
        y = 0
        for row in self._rows:
            self._tops.append(y)
            y += self._height(row)
        self._total = y
        self.canvas.configure(scrollregion=(0, 0, 1, max(y, 1)))
        if self.selected is not None and not any(r.key == self.selected for r in self._rows):
            self.selected = None
        self._redraw()

    @property
    def rows(self) -> list[Row]:
        return list(self._rows)

    def item_keys(self) -> list[int]:
        return [r.key for r in self._rows if r.kind == "item"]

    def select(self, key: int | None, *, notify: bool = True) -> None:
        self.selected = key
        if key is not None:
            self._scroll_into_view(key)
        self._redraw()
        if notify:
            self.on_select(key)

    def visible_keys(self) -> list[int]:
        return list(self._visible)

    def fit_height(self, max_height: int) -> None:
        """Ask for just enough height to show every row, but no more than max_height."""
        cap = round(max_height * self._get_widget_scaling())
        self.canvas.configure(height=max(min(self._total, cap), self._row_h))

    def _height(self, row: Row) -> int:
        return {"group": self._group_h, "section": self._section_h, "band": self._band_h}.get(
            row.kind, self._row_h
        )

    def action_point(self, key: int, action: str) -> tuple[int, int]:
        """The middle of a band's button in window coordinates (for tests)."""
        for x0, y0, x1, y1, hit_key, hit_action in self._hits:
            if hit_key == key and hit_action == action:
                return (x0 + x1) // 2, round((y0 + y1) / 2 - self.canvas.canvasy(0))
        raise KeyError((key, action))

    def _index(self, key: int) -> int | None:
        return next((i for i, r in enumerate(self._rows) if r.key == key), None)

    def check_point(self, key: int) -> int:
        """An x inside the row's tick box (for tests and keyboard helpers)."""
        return self._pad + 4 + self._box // 2

    def row_middle(self, key: int) -> int:
        """The row's vertical middle in window coordinates."""
        index = self._index(key)
        if index is None:
            raise KeyError(key)
        row_h = self._height(self._rows[index])
        return round(self._tops[index] - self.canvas.canvasy(0) + row_h / 2)

    # --- scrolling -------------------------------------------------------------------------

    def _on_scrollbar(self, *args: Any) -> None:
        self.canvas.yview(*args)
        self._redraw()

    def _on_wheel(self, event: Any) -> str | None:
        self.canvas.yview_scroll(int(-event.delta / 120 * self._row_h), "units")
        self._redraw()
        # Inside a scrolling page, only this list moves while it has rows left to show.
        return "break" if self._total > self.canvas.winfo_height() else None

    def scroll_to_fraction(self, fraction: float) -> None:
        self.canvas.yview_moveto(fraction)
        self._redraw()

    def _scroll_into_view(self, key: int) -> None:
        index = self._index(key)
        if index is None or not self._total:
            return
        top = self._tops[index]
        bottom = top + self._row_h
        view_top = self.canvas.canvasy(0)
        view_h = self.canvas.winfo_height()
        if top < view_top:
            self.canvas.yview_moveto(top / self._total)
        elif bottom > view_top + view_h:
            self.canvas.yview_moveto(max(bottom - view_h, 0) / self._total)

    # --- drawing ---------------------------------------------------------------------------

    def _schedule_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self._redraw)

    def _row_at(self, y: float) -> int | None:
        index = bisect.bisect_right(self._tops, y) - 1
        if 0 <= index < len(self._rows):
            return index
        return None

    def _redraw(self) -> None:
        self._redraw_pending = False
        c = self.canvas
        c.delete("all")
        self._visible = []
        self._hits = []
        if not self._rows:
            return
        width = max(c.winfo_width(), 200)
        top = c.canvasy(0)
        height = max(c.winfo_height(), self._row_h)
        first = self._row_at(top) or 0
        for index in range(first, len(self._rows)):
            y = self._tops[index]
            if y > top + height:
                break
            row = self._rows[index]
            self._visible.append(row.key)
            if row.kind == "group":
                self._draw_group(row, y, width)
            elif row.kind == "section":
                self._draw_section(row, y, width)
            elif row.kind == "band":
                self._draw_band(row, y, width)
            elif row.code or row.faint:
                self._draw_line(row, y, width)
            else:
                self._draw_item(row, y, width)

    def refresh_images(self) -> None:
        """Draw again once pictures have arrived."""
        self._schedule_redraw()

    def _draw_group(self, row: Row, y: int, width: int) -> None:
        p, pad = self.palette, self._pad
        text = row.title.upper()
        if row.subtitle:
            text += f"   ·   {row.subtitle}"
        self.canvas.create_text(
            pad + 4,
            y + self._group_h - 7,
            text=_fit(text, self._group_font, width - 2 * pad),
            anchor="sw",
            fill=p.muted,
            font=self._group_font,
        )

    def _draw_section(self, row: Row, y: int, width: int) -> None:
        p, pad = self.palette, self._pad
        text = row.title.upper() + (f"   ·   {row.subtitle}" if row.subtitle else "")
        self.canvas.create_text(
            pad + 4,
            y + self._section_h - 8,
            text=_fit(text, self._group_font, width - 2 * pad),
            anchor="sw",
            fill=p.muted,
            font=self._group_font,
        )

    def _draw_band(self, row: Row, y: int, width: int) -> None:
        c, p, pad = self.canvas, self.palette, self._pad
        x0, x1 = pad // 2, width - pad // 2
        y0, y1 = y + 3, y + self._band_h - 3
        inset = round(6 * self._get_widget_scaling())
        poster_h = y1 - y0 - 2 * inset
        poster_w = round(poster_h * 2 / 3)
        poster, backdrop = self.images(row, x1 - x0, y1 - y0)
        colour = THUMB_COLOURS[zlib.crc32((row.badge or row.title).encode()) % len(THUMB_COLOURS)]
        if backdrop is not None:
            c.create_image(x0, y0, image=backdrop, anchor="nw", tags=("band-image",))
        else:
            c.create_rectangle(x0, y0, x1, y1, fill=p.surface, width=0, tags=("band-fill",))
            c.create_rectangle(x0, y0, x0 + 3, y1, fill=colour, width=0)
        px, py = x0 + round(8 * self._get_widget_scaling()), y0 + inset
        if poster is not None:
            c.create_image(px, py, image=poster, anchor="nw", tags=("band-image",))
        else:
            c.create_rectangle(px, py, px + poster_w, py + poster_h, fill=colour, width=0)
            initials = "".join(w[0] for w in (row.badge or row.title).split()[:2]).upper()
            c.create_text(
                px + poster_w / 2,
                py + poster_h / 2,
                text=initials,
                fill="#FFFFFF",
                font=self._thumb_font,
                tags=("band-initials",),
            )
        right = x1 - round(10 * self._get_widget_scaling())
        middle = (y0 + y1) / 2
        for index, (action, label) in enumerate(reversed(row.actions)):
            w = self._button_font.measure(label) + round(22 * self._get_widget_scaling())
            bh = round(26 * self._get_widget_scaling())
            bx0, by0, bx1, by1 = right - w, round(middle - bh / 2), right, round(middle + bh / 2)
            primary = index == 0
            c.create_rectangle(
                bx0,
                by0,
                bx1,
                by1,
                fill=p.accent if primary else "",
                outline=p.accent if primary else p.edge,
                width=1,
            )
            c.create_text(
                (bx0 + bx1) / 2,
                middle,
                text=label,
                fill=p.accent_text if primary else p.text,
                font=self._button_font,
            )
            self._hits.append((bx0, by0, bx1, by1, row.key, action))
            right = bx0 - round(8 * self._get_widget_scaling())
        if row.note:
            c.create_text(
                right, middle, text=row.note, anchor="e", fill=p.muted, font=self._sub_font
            )
            right -= self._sub_font.measure(row.note) + round(12 * self._get_widget_scaling())
        tx = px + poster_w + round(12 * self._get_widget_scaling())
        avail = right - tx
        c.create_text(
            tx,
            y0 + (y1 - y0) * 0.36,
            text=_fit(row.title, self._band_font, avail),
            anchor="w",
            fill=p.text,
            font=self._band_font,
        )
        c.create_text(
            tx,
            y0 + (y1 - y0) * 0.72,
            text=_fit(row.subtitle, self._band_sub_font, avail),
            anchor="w",
            fill=p.muted,
            font=self._band_sub_font,
        )

    def _draw_line(self, row: Row, y: int, width: int) -> None:
        """A file line under a band: code, name, the old name faint on the right."""
        c, p, pad, h = self.canvas, self.palette, self._pad, self._row_h
        selected = row.key == self.selected
        if selected or row.key == self._hover:
            c.create_rectangle(
                pad // 2,
                y + 1,
                width - pad // 2,
                y + h - 1,
                fill=p.raised if selected else p.surface,
                width=0,
            )
        if selected:
            c.create_rectangle(pad // 2, y + 1, pad // 2 + 3, y + h - 1, fill=p.accent, width=0)
        middle = y + h / 2
        x = self._line_x
        if row.checked is not None:
            self._draw_box(row, pad + 4, round(middle - self._box / 2))
        right = width - pad - 30
        if row.faint:
            faint_w = min(self._sub_font.measure(row.faint), round((right - x) * 0.45))
            c.create_text(
                right,
                middle,
                text=_fit(row.faint, self._sub_font, faint_w),
                anchor="e",
                fill=p.muted,
                font=self._sub_font,
            )
            right -= faint_w + round(16 * self._get_widget_scaling())
        if row.code:
            c.create_text(x, middle, text=row.code, anchor="w", fill=p.accent, font=self._code_font)
            x += max(self._code_font.measure("S00E00-E00"), self._code_font.measure(row.code))
            x += round(10 * self._get_widget_scaling())
        c.create_text(
            x,
            middle,
            text=_fit(row.title, self._line_font, right - x),
            anchor="w",
            fill=p.text,
            font=self._line_font,
        )
        if row.key == self._hover and self.removable:
            c.create_text(width - pad - 12, middle, text="✕", fill=p.muted, font=self._status_font)
        else:
            glyph, colour = self._status_style.get(row.status, ("", p.muted))
            c.create_text(width - pad - 12, middle, text=glyph, fill=colour, font=self._status_font)

    def _draw_item(self, row: Row, y: int, width: int) -> None:
        c, p, pad, h = self.canvas, self.palette, self._pad, self._row_h
        selected = row.key == self.selected
        if selected or row.key == self._hover:
            c.create_rectangle(
                pad // 2,
                y + 2,
                width - pad // 2,
                y + h - 2,
                fill=p.raised if selected else p.surface,
                width=0,
            )
        if selected:
            c.create_rectangle(pad // 2, y + 2, pad // 2 + 3, y + h - 2, fill=p.accent, width=0)

        tx = pad + 4
        if row.checked is not None:
            self._draw_box(row, tx, y + (h - self._box) // 2)
            tx += self._box + 10
        if self.thumbnails:
            thumb_w, thumb_h = round(h * 0.55), h - 2 * round(h * 0.13)
            ty = y + (h - thumb_h) // 2
            colour = THUMB_COLOURS[zlib.crc32(row.badge.encode()) % len(THUMB_COLOURS)]
            c.create_rectangle(tx, ty, tx + thumb_w, ty + thumb_h, fill=colour, width=0)
            initials = "".join(w[0] for w in row.badge.split()[:2]).upper()
            c.create_text(
                tx + thumb_w / 2,
                ty + thumb_h / 2,
                text=initials,
                fill="#ffffff",
                font=self._thumb_font,
            )
            tx += thumb_w + 10

        right = width - pad - 30
        x = tx
        avail = right - x
        title = _fit(row.title, self._title_font, avail)
        title_y = y + h * 0.36
        if row.highlight and title == row.title:
            start, end = row.highlight
            for part, colour in (
                (title[:start], p.text),
                (title[start:end], p.accent),
                (title[end:], p.text),
            ):
                if part:
                    c.create_text(
                        x, title_y, text=part, anchor="w", fill=colour, font=self._title_font
                    )
                    x += self._title_font.measure(part)
        else:
            c.create_text(x, title_y, text=title, anchor="w", fill=p.text, font=self._title_font)
        c.create_text(
            tx,
            y + h * 0.70,
            text=_fit(row.subtitle, self._sub_font, avail),
            anchor="w",
            fill=p.muted,
            font=self._sub_font,
        )

        if row.key == self._hover and self.removable:
            c.create_text(
                width - pad - 12, y + h / 2, text="✕", fill=p.muted, font=self._status_font
            )
        else:
            glyph, colour = self._status_style.get(row.status, ("", p.muted))
            c.create_text(
                width - pad - 12, y + h / 2, text=glyph, fill=colour, font=self._status_font
            )

    def _draw_box(self, row: Row, x: int, y: int) -> None:
        c, p, b = self.canvas, self.palette, self._box
        if row.checked:
            fill = p.border if row.check_disabled else p.accent
            c.create_rectangle(x, y, x + b, y + b, fill=fill, outline=fill, width=1)
            c.create_line(
                x + b * 0.22,
                y + b * 0.52,
                x + b * 0.42,
                y + b * 0.72,
                x + b * 0.78,
                y + b * 0.3,
                fill=p.muted if row.check_disabled else p.accent_text,
                width=max(2, b // 8),
            )
        else:
            c.create_rectangle(x, y, x + b, y + b, outline=p.border, width=max(1, b // 10))

    # --- mouse -----------------------------------------------------------------------------

    def _set_hover(self, key: int | None) -> None:
        if key != self._hover:
            self._hover = key
            self._redraw()

    def _on_motion(self, event: Any) -> None:
        index = self._row_at(self.canvas.canvasy(event.y))
        row = self._rows[index] if index is not None else None
        self._set_hover(row.key if row and row.kind == "item" else None)

    def _on_click(self, event: Any) -> None:
        self.canvas.focus_set()
        cy = self.canvas.canvasy(event.y)
        for x0, y0, x1, y1, key, action in self._hits:
            if x0 <= event.x <= x1 and y0 <= cy <= y1:
                self.on_action(key, action)
                return
        index = self._row_at(cy)
        if index is None or self._rows[index].kind != "item":
            return
        row = self._rows[index]
        box_right = self._pad + 4 + self._box + 6
        if row.checked is not None and event.x <= box_right:
            if not row.check_disabled:
                row.checked = not row.checked
                self._redraw()
                self.on_toggle(row.key, row.checked)
        elif self.removable and event.x > self.canvas.winfo_width() - self._pad - 28:
            self.on_remove(row.key)
        else:
            self.select(row.key)
