"""A CustomTkinter root window that also accepts files dragged in from Explorer."""

from __future__ import annotations

from typing import Any

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD


class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)  # pyright: ignore[reportPrivateUsage]
