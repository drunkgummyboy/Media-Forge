"""Shared setup for pop-up windows.

Tk on Windows can crash later if a window marked `transient` (with a size set) is
destroyed before it was ever shown. Pop-ups therefore stay on top with -topmost instead,
which the old MediaForge did as well.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from mediaforge.ui.theme import ASSETS

ICON = ASSETS / "MediaForge.ico"


def prepare_popup(window: ctk.CTkToplevel) -> None:
    window.attributes("-topmost", True)
    window.lift()
    window.after(50, _focus, window)
    window.after(250, _set_icon, window)  # CustomTkinter sets its own icon at 200 ms


def _set_icon(window: ctk.CTkToplevel) -> None:
    if window.winfo_exists() and ICON.exists():
        try:
            window.iconbitmap(str(ICON))
        except tk.TclError:
            pass


def _focus(window: ctk.CTkToplevel) -> None:
    if window.winfo_exists():
        window.focus_force()
