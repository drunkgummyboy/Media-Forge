"""The Studio look: dark and calm with one warm accent, plus a light variant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import customtkinter as ctk

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
ASSETS = Path(__file__).resolve().parent.parent / "assets"


@dataclass(frozen=True)
class Palette:
    name: str
    main: str
    rail: str
    surface: str
    raised: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    success: str
    error: str
    warning: str


STUDIO_DARK = Palette(
    name="Dark",
    main="#16181d",
    rail="#111317",
    surface="#1e2128",
    raised="#262a33",
    border="#2c303a",
    text="#e8eaed",
    muted="#8b93a1",
    accent="#f2a33a",
    accent_text="#16181d",
    success="#4cc38a",
    error="#e5534b",
    warning="#f2a33a",
)

STUDIO_LIGHT = Palette(
    name="Light",
    main="#f6f7f9",
    rail="#eceef2",
    surface="#ffffff",
    raised="#e9ebf0",
    border="#d8dbe2",
    text="#1b1f2a",
    muted="#5c6473",
    accent="#d9861c",
    accent_text="#ffffff",
    success="#2e9e6a",
    error="#c9423a",
    warning="#d9861c",
)


def palette_for(theme: str) -> Palette:
    """Dark or Light; "System" follows Windows' app mode."""
    if theme == "System":
        ctk.set_appearance_mode("System")
        theme = ctk.get_appearance_mode()
    return STUDIO_LIGHT if theme == "Light" else STUDIO_DARK


def font(size: int = 12, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FAMILY if mono else FONT_FAMILY, size=size, weight=weight)  # type: ignore[arg-type]
