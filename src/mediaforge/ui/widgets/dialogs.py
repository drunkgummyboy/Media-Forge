"""Small Studio-styled question windows that can be awaited."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import customtkinter as ctk

from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.popups import prepare_popup


async def ask_choice(
    master: Any, palette: Palette, title: str, message: str, choices: Sequence[str]
) -> str | None:
    """Show a question with buttons; returns the chosen label, or None if closed.

    The first choice is the main (accent) button.
    """
    future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    win = ctk.CTkToplevel(master)
    win.title(title)
    win.configure(fg_color=palette.main)
    win.resizable(False, False)
    prepare_popup(win)

    def finish(value: str | None) -> None:
        if not future.done():
            future.set_result(value)
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    ctk.CTkLabel(win, text=title, font=font(15, "bold"), text_color=palette.text).pack(
        anchor="w", padx=20, pady=(18, 4)
    )
    ctk.CTkLabel(
        win,
        text=message,
        font=font(12),
        text_color=palette.muted,
        justify="left",
        wraplength=420,
    ).pack(anchor="w", padx=20, pady=(0, 14))
    buttons = ctk.CTkFrame(win, fg_color="transparent")
    buttons.pack(fill="x", padx=20, pady=(0, 18))
    for index, label in enumerate(reversed(list(choices))):
        main = index == len(choices) - 1
        ctk.CTkButton(
            buttons,
            text=label,
            height=30,
            fg_color=palette.accent if main else "transparent",
            text_color=palette.accent_text if main else palette.text,
            hover_color=palette.raised,
            border_width=0 if main else 1,
            border_color=palette.border,
            font=font(12, "bold" if main else "normal"),
            command=lambda v=label: finish(v),
        ).pack(side="right", padx=(8, 0))
    win.after(10, win.grab_set)
    return await future


async def confirm(master: Any, palette: Palette, title: str, message: str, yes: str) -> bool:
    return await ask_choice(master, palette, title, message, [yes, "Cancel"]) == yes
