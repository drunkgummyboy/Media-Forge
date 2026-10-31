"""Small on/off pills, as in the Studio mockup's bottom bar."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from mediaforge.ui.theme import Palette, font


class Chip(ctk.CTkButton):
    def __init__(
        self,
        master: Any,
        text: str,
        palette: Palette,
        on_toggle: Callable[[bool], None],
        value: bool = False,
    ) -> None:
        self._label = text
        self._palette = palette
        self._on_toggle = on_toggle
        self.value = value
        super().__init__(
            master,
            text=text,
            width=10,
            height=26,
            corner_radius=13,
            border_width=1,
            fg_color="transparent",
            hover_color=palette.raised,
            font=font(11),
            command=self._clicked,
        )
        self._paint()

    def _paint(self) -> None:
        p = self._palette
        self.configure(
            text=("✓ " if self.value else "") + self._label,
            border_color=p.accent if self.value else p.border,
            text_color=p.accent if self.value else p.muted,
        )

    def set(self, value: bool) -> None:
        self.value = value
        self._paint()

    def _clicked(self) -> None:
        self.set(not self.value)
        self._on_toggle(self.value)
