"""The small window shown while missing packages are installed at launch.

Plain tkinter only, with the Studio colours copied in: customtkinter (and so ui/theme.py) may be
exactly what is missing. Ported from AirGrabber's bootstrap window.
"""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import messagebox, ttk

from mediaforge.platform.bootstrap import (
    Installer,
    Problem,
    echo,
    failure_message,
    run_in_console,
    to_console,
)

_ICON_FILE = Path(__file__).resolve().parent.parent / "assets" / "MediaForge.ico"
# Copies of the dark Slate palette's colours (ui/theme.py needs customtkinter).
_BG = "#161616"
_SURFACE = "#262626"
_ACCENT = "#4E6A85"
_TEXT = "#FFFFFF"
_MUTED = "#A4B2C6"
_POLL_MS = 150
_STATUS_CHARS = 64


def run_with_window(
    commands: Sequence[Sequence[str]], log_file: Path, problems: Sequence[Problem]
) -> int | None:
    """Run the install with a progress window (console output when Tk can't open one).

    Returns the exit code, or None when the user cancelled.
    """
    installer = Installer(commands, log_file)
    try:
        try:
            window = _ProgressWindow(installer, problems)
        except tk.TclError:
            installer.close()
            return run_in_console(commands, log_file, problems)
        return window.run()
    finally:
        installer.close()


def report_failure(problems: Sequence[Problem], log_file: Path) -> None:
    message = failure_message(problems, log_file)
    to_console(message)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("MediaForge", message, parent=root)
        root.destroy()
    except tk.TclError:
        pass


class _ProgressWindow:
    """Drives an Installer and shows pip's latest line."""

    def __init__(self, installer: Installer, problems: Sequence[Problem]) -> None:
        self._installer = installer
        self._cancelled = False
        self._after_id: str | None = None
        _enable_dpi_awareness()
        self._root = root = tk.Tk()
        root.title("MediaForge")
        if sys.platform == "win32" and _ICON_FILE.is_file():
            root.iconbitmap(str(_ICON_FILE))
        root.configure(bg=_BG, padx=24, pady=20)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._cancel)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(
            "Setup.Horizontal.TProgressbar",
            troughcolor=_SURFACE,
            background=_ACCENT,
            bordercolor=_BG,
            lightcolor=_ACCENT,
            darkcolor=_ACCENT,
        )
        names = ", ".join(problem.requirement.name for problem in problems)
        tk.Label(
            root,
            text="Getting MediaForge ready",
            font=("Segoe UI", 12, "bold"),
            bg=_BG,
            fg=_TEXT,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            root,
            text=f"Installing the parts it needs: {names}",
            font=("Segoe UI", 9),
            bg=_BG,
            fg=_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 12))
        bar = ttk.Progressbar(root, mode="indeterminate", style="Setup.Horizontal.TProgressbar")
        bar.pack(fill="x")
        bar.start(12)
        self._status = tk.Label(
            root,
            text="Starting…",
            width=_STATUS_CHARS,
            font=("Segoe UI", 9),
            bg=_BG,
            fg=_MUTED,
            anchor="w",
        )
        self._status.pack(fill="x", pady=(10, 0))

        root.update_idletasks()
        _dark_title_bar(root)
        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
        y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
        root.geometry(f"+{x}+{y}")

    def run(self) -> int | None:
        self._after_id = self._root.after(0, self._tick)
        self._root.mainloop()
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
        self._root.destroy()
        return None if self._cancelled else self._installer.returncode

    def _tick(self) -> None:
        lines = self._installer.poll()
        echo(lines)
        if lines:
            self._status.configure(text=_shorten(lines[-1].strip(), _STATUS_CHARS))
        if self._installer.finished:
            self._after_id = None
            self._root.quit()
        else:
            self._after_id = self._root.after(_POLL_MS, self._tick)

    def _cancel(self) -> None:
        self._cancelled = True
        self._installer.cancel()
        self._root.quit()


def _enable_dpi_awareness() -> None:
    """The call customtkinter makes on start-up; done first so this window is sharp at 150 %."""
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def _dark_title_bar(window: tk.Tk) -> None:
    """Windows 10/11 dark title bar, matching the windows that follow."""
    if sys.platform == "win32":
        import ctypes

        try:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            value = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before 20H1)
                if (
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), 4
                    )
                    == 0
                ):
                    break
        except (AttributeError, OSError):
            pass


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
