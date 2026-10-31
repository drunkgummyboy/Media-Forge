"""The collapsible Activity strip: log lines from anywhere in the app, coloured by kind."""

from __future__ import annotations

import logging
from typing import Any

import customtkinter as ctk

from mediaforge.logging_setup import UiLogBridge
from mediaforge.ui.theme import Palette, font

MAX_LINES = 1000
POLL_MS = 150


class LogStrip(ctk.CTkFrame):
    def __init__(self, master: Any, bridge: UiLogBridge, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.surface, corner_radius=0)
        self._bridge = bridge
        self._palette = palette
        self._open = True
        self._poll_id: str | None = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(6, 0))
        self._toggle = ctk.CTkButton(
            header,
            text="▾  Activity",
            width=90,
            height=22,
            fg_color="transparent",
            hover_color=palette.raised,
            text_color=palette.muted,
            anchor="w",
            font=font(11, "bold"),
            command=self.toggle,
        )
        self._toggle.pack(side="left")
        self._box = ctk.CTkTextbox(
            self,
            height=110,
            fg_color=palette.surface,
            text_color=palette.text,
            font=font(11, mono=True),
            wrap="none",
            activate_scrollbars=True,
        )
        self._box.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self._box.tag_config("error", foreground=palette.error)
        self._box.tag_config("warning", foreground=palette.warning)
        self._box.tag_config("success", foreground=palette.success)
        self._box.configure(state="disabled")
        self._poll_id = self.after(POLL_MS, self._poll)

    def toggle(self) -> None:
        self._open = not self._open
        if self._open:
            self._box.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        else:
            self._box.pack_forget()
        self._toggle.configure(text=("▾  Activity" if self._open else "▸  Activity"))

    def _poll(self) -> None:
        lines = self._bridge.drain()
        if lines:
            self._box.configure(state="normal")
            for line in lines:
                if line.level >= logging.ERROR:
                    tag = "error"
                elif line.level >= logging.WARNING:
                    tag = "warning"
                elif line.success:
                    tag = "success"
                else:
                    tag = ""
                self._box.insert("end", line.text + "\n", tag or None)
            excess = int(self._box.index("end-1c").split(".")[0]) - MAX_LINES
            if excess > 0:
                self._box.delete("1.0", f"{excess + 1}.0")
            self._box.see("end")
            self._box.configure(state="disabled")
        self._poll_id = self.after(POLL_MS, self._poll)

    def clear(self) -> None:
        self._box.configure(state="normal")
        self._box.delete("1.0", "end")
        self._box.configure(state="disabled")

    def text(self) -> str:
        return self._box.get("1.0", "end")

    def destroy(self) -> None:
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()
