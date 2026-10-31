"""AirGrabber's family look: its colours, in light and dark, with its ten accent colours.

Every view takes a `Palette`: one look (light or dark) of one accent, chosen when MediaForge starts
(Settings → Looks; a change takes a restart, as in AirGrabber). The colours come from
ui/palettes.py, a port of AirGrabber's, so the two programs look like one family.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import customtkinter as ctk

from mediaforge.ui import palettes

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
ACCENTS: tuple[str, ...] = tuple(palettes.PALETTES)  # "slate" first
# Calm tints behind a poster's initials when there is no picture (same in every look).
TINTS = ("#5B3FA8", "#A8742B", "#1F8A55", "#B05A26", "#2F6F9A", "#8A3F6B", "#4D6B2F")


@dataclass(frozen=True)
class Palette:
    name: str  # "Dark" or "Light"
    main: str  # window background
    rail: str  # the top band
    surface: str  # cards, inputs, bands
    raised: str  # quiet buttons, chips, hover
    border: str  # quiet 1 px lines
    text: str
    muted: str
    accent: str  # primary buttons, chosen items
    accent_text: str  # text on the accent
    success: str
    error: str
    warning: str  # amber: something needs a look
    chip: str
    selected: str  # the chosen part of a switch (light: a tint, so its text stays readable)
    selected_hover: str
    link: str
    accent_hover: str
    edge: str  # outlined buttons
    on_color: str = "#FFFFFF"  # text on a coloured fill (badges, initials)


def accent_label(accent: str) -> str:
    """The accent's name as Settings shows it ("Petrol")."""
    palette = palettes.PALETTES.get(accent, palettes.PALETTES["slate"])
    return palette.name


def accent_swatch(accent: str) -> str:
    return palettes.PALETTES.get(accent, palettes.PALETTES["slate"]).accent


def _build(look: str, accent: str) -> Palette:
    index = 1 if look == "Dark" else 0
    tok = {**palettes.tokens(accent), **palettes.FIXED}

    def c(name: str) -> str:
        return palettes.to_hex(tok[name][index])

    return Palette(
        name=look,
        main=c("BG"),
        rail=c("TAB_BG"),
        surface=c("SURFACE"),
        raised=c("CHIP"),
        border=c("PLACEHOLDER"),
        text=c("TEXT"),
        muted=c("TEXT_MUTED"),
        accent=c("ACCENT"),
        accent_text=c("ON_ACCENT"),
        success=c("SUCCESS"),
        error=c("DANGER"),
        warning=c("WARNING"),
        chip=c("CHIP"),
        selected=c("SELECTED"),
        selected_hover=c("SELECTED_HOVER"),
        link=c("LINK"),
        accent_hover=c("ACCENT_HOVER"),
        edge=c("EDGE"),
    )


DARK = _build("Dark", "slate")
LIGHT = _build("Light", "slate")


def palette_for(theme: str, accent: str = "slate") -> Palette:
    """Dark or Light ("System" follows Windows' app mode) in `accent` (unknown: Slate)."""
    if theme == "System":
        ctk.set_appearance_mode("System")
        theme = ctk.get_appearance_mode()
    look = "Light" if theme == "Light" else "Dark"
    if accent not in palettes.PALETTES:
        accent = "slate"
    if accent == "slate":
        return LIGHT if look == "Light" else DARK
    return _build(look, accent)


def font(size: int = 12, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FAMILY if mono else FONT_FAMILY, size=size, weight=weight)  # type: ignore[arg-type]
