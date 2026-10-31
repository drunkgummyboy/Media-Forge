"""AirGrabber's accent colours (Settings → Looks): ten calm palettes, Slate first.

Ported from AirGrabber's app/ui/palettes.py and the Slate values of its app/ui/theme.py, so the
two programs look like one family. Each palette gives the buttons, chosen items, links and borders
its colour and the backgrounds a very slight tint of it, in the light and the dark look alike.
Slate is AirGrabber's own slate blue, exactly as its theme.py writes it; the other palettes'
colours come from AirGrabber's design, and a token the design doesn't give is Slate's value moved
the way a related one moves (`shifted`). Green, amber, red and the text colours are the same in
every palette.

Pure: `tokens(accent)` gives every accent-dependent token as a (light, dark) pair.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

type Pair = tuple[str, str]  # (light, dark)

# AirGrabber's theme.py values (Slate), for the tokens a palette changes.
SLATE: dict[str, Pair] = {
    "BG": ("#F2F4F7", "#161616"),
    "SURFACE": ("#FFFFFF", "#242424"),
    "TAB_BG": ("#F8F9FB", "#1C1C1C"),
    "EDGE": ("#768DA4", "#4E6A85"),
    "ACCENT": ("#4E6A85", "#4E6A85"),
    "ACCENT_HOVER": ("#427839", "#427839"),
    "LINK": ("#2D6390", "#5D8AA8"),
    "PLACEHOLDER": ("#DCE2E9", "gray20"),
    "SELECTED": ("#C9D8E8", "#4E6A85"),
    "SELECTED_HOVER": ("#B8CADD", "#427839"),
    "CHIP": ("#E6EBF1", "gray15"),
}

# The same in every palette.
FIXED: dict[str, Pair] = {
    "TEXT": ("#1B2530", "#FFFFFF"),
    "TEXT_MUTED": ("#4D5B6A", "#A4B2C6"),
    # AirGrabber's dark value is #DCE4EE; pure white keeps teal's and sage's hover readable.
    "ON_ACCENT": ("#FFFFFF", "#FFFFFF"),
    "SUCCESS": ("#1A6F47", "#2FA572"),
    "DANGER": ("#B5302A", "#E74C3C"),
    "WARNING": ("#9C5100", "#F39C12"),
}


@dataclass(frozen=True, slots=True)
class Shades:
    """One look's (light or dark) colours of a palette, as AirGrabber's design gives them."""

    bg: str
    surface: str
    chip: str
    line: str  # PLACEHOLDER
    edge: str
    rowsel: str
    todaybg: str
    link: str


@dataclass(frozen=True, slots=True)
class AccentPalette:
    name: str
    accent: str
    hover: str
    light: Shades
    dark: Shades


# In the order Settings shows them; Slate's shades are the anchors the others move from.
PALETTES: dict[str, AccentPalette] = {
    "slate": AccentPalette(
        "Slate",
        "#4E6A85",
        "#61809D",
        light=Shades(
            "#F2F4F7", "#FFFFFF", "#E6EBF1", "#DCE2E9", "#768DA4", "#D7E3F0", "#DEE8F3", "#2D6390"
        ),
        dark=Shades(
            "#161616", "#222324", "#2A2B2D", "#2E3033", "#4E6A85", "#2A3A4D", "#182133", "#7FA3C4"
        ),
    ),
    "ocean": AccentPalette(
        "Ocean",
        "#3A6EA5",
        "#4C82BA",
        light=Shades(
            "#F1F4F8", "#FFFFFF", "#E2EAF3", "#D5DFEA", "#7B9DC4", "#D3E2F3", "#DDE8F5", "#2C5F95"
        ),
        dark=Shades(
            "#141619", "#1F2329", "#272D35", "#2C323A", "#3A6EA5", "#1F3550", "#162438", "#86B1E0"
        ),
    ),
    "petrol": AccentPalette(
        "Petrol",
        "#2F6676",
        "#3D7B8C",
        light=Shades(
            "#F0F4F5", "#FFFFFF", "#E0EAEC", "#D3E0E3", "#7099A4", "#CFE2E7", "#DBEBEF", "#275868"
        ),
        dark=Shades(
            "#131617", "#1E2325", "#262D30", "#2B3336", "#3F7585", "#1E363D", "#15262B", "#79B3C3"
        ),
    ),
    "teal": AccentPalette(
        "Teal",
        "#2E7A76",
        "#3B918C",
        light=Shades(
            "#F0F5F4", "#FFFFFF", "#E0ECEA", "#D3E2DF", "#6E9E99", "#CFE6E3", "#DCEFEC", "#23706B"
        ),
        dark=Shades(
            "#131716", "#1E2423", "#26302E", "#2A3432", "#3E7C77", "#1E3937", "#142826", "#6FBDB6"
        ),
    ),
    "sage": AccentPalette(
        "Sage",
        "#5E7A66",
        "#718E79",
        light=Shades(
            "#F2F5F2", "#FFFFFF", "#E4EBE5", "#D8E1D9", "#8DA593", "#DAE6DC", "#E3EDE5", "#4A6652"
        ),
        dark=Shades(
            "#151715", "#212421", "#2A2E2A", "#2F332F", "#5E7A66", "#2C3A30", "#1D2820", "#A3C2AB"
        ),
    ),
    "indigo": AccentPalette(
        "Indigo",
        "#5A5FA3",
        "#6D72B6",
        light=Shades(
            "#F3F3F8", "#FFFFFF", "#E6E6F2", "#DADAEA", "#8A8DC2", "#DCDDF2", "#E4E5F6", "#4A4F98"
        ),
        dark=Shades(
            "#151519", "#212128", "#2A2A34", "#2F2F3A", "#5A5FA3", "#2C2D4A", "#1C1D33", "#9EA2E0"
        ),
    ),
    "plum": AccentPalette(
        "Plum",
        "#7A5786",
        "#8D6A99",
        light=Shades(
            "#F6F2F7", "#FFFFFF", "#EDE4F0", "#E2D6E6", "#A585AF", "#E8DAEC", "#F0E4F3", "#6E4A7B"
        ),
        dark=Shades(
            "#171518", "#242026", "#2E2830", "#332C36", "#7A5786", "#3A2B40", "#271D2B", "#C29ACF"
        ),
    ),
    "rose": AccentPalette(
        "Rose",
        "#9E5A6C",
        "#B16D7F",
        light=Shades(
            "#F8F2F3", "#FFFFFF", "#F0E3E6", "#E8D6DB", "#C08A98", "#F1DCE2", "#F5E4E8", "#8C4A5C"
        ),
        dark=Shades(
            "#181516", "#262022", "#30282B", "#362D30", "#9E5A6C", "#432A33", "#2C1C22", "#D79AAB"
        ),
    ),
    "mocha": AccentPalette(
        "Mocha",
        "#7A6555",
        "#8D7868",
        light=Shades(
            "#F6F3F1", "#FFFFFF", "#EDE6E1", "#E2D9D2", "#A6907F", "#EADFD7", "#F1E7DF", "#6A5546"
        ),
        dark=Shades(
            "#171615", "#242120", "#2E2A28", "#34302D", "#7A6555", "#3A3029", "#29211C", "#CDB4A0"
        ),
    ),
    "graphite": AccentPalette(
        "Graphite",
        "#5D6670",
        "#717B86",
        light=Shades(
            "#F3F4F5", "#FFFFFF", "#E7E9EB", "#DCDFE2", "#8E969E", "#DDE1E5", "#E6E9EC", "#4A535C"
        ),
        dark=Shades(
            "#151515", "#222222", "#2B2B2B", "#303030", "#5D6670", "#33383F", "#22262B", "#A9B3BE"
        ),
    ),
}

# Tokens the design gives per palette, by their Shades field (both looks).
_FROM_SHADES: dict[str, str] = {
    "BG": "bg",
    "SURFACE": "surface",
    "CHIP": "chip",
    "PLACEHOLDER": "line",
    "EDGE": "edge",
    "LINK": "link",
}

# Every other token, per look (light, dark): "=accent" / "=hover" = the palette's own colour;
# a Shades field = Slate's value moved the way that field moves from Slate to the palette.
_DERIVED: dict[str, Pair] = {
    "TAB_BG": ("bg", "bg"),
    "SELECTED": ("rowsel", "=accent"),
    "SELECTED_HOVER": ("rowsel", "=hover"),
}

NEUTRAL = 0.02  # a saturation below this is a grey: no hue of its own to turn


def tokens(accent: str) -> dict[str, Pair]:
    """Every accent-dependent token for `accent` (unknown names: Slate), hex in both looks."""
    if accent not in PALETTES or accent == "slate":
        return {name: (to_hex(light), to_hex(dark)) for name, (light, dark) in SLATE.items()}
    palette = PALETTES[accent]
    anchors = PALETTES["slate"]
    out: dict[str, Pair] = {}
    for name, (base_light, base_dark) in SLATE.items():
        if name == "ACCENT":
            out[name] = (palette.accent, palette.accent)
        elif name == "ACCENT_HOVER":
            out[name] = (palette.hover, palette.hover)
        elif name in _FROM_SHADES:
            field = _FROM_SHADES[name]
            out[name] = (getattr(palette.light, field), getattr(palette.dark, field))
        else:
            light_rule, dark_rule = _DERIVED[name]
            out[name] = (
                _derive(light_rule, base_light, palette, palette.light, anchors.light),
                _derive(dark_rule, base_dark, palette, palette.dark, anchors.dark),
            )
    return {name: (to_hex(light), to_hex(dark)) for name, (light, dark) in out.items()}


def _derive(rule: str, base: str, palette: AccentPalette, own: Shades, anchors: Shades) -> str:
    if rule == "=accent":
        return palette.accent
    if rule == "=hover":
        return palette.hover
    return shifted(base, getattr(anchors, rule), getattr(own, rule))


# -- colour arithmetic (pure) -------------------------------------------------------------------


def to_hex(color: str) -> str:
    """ "#RRGGBB" for "#rrggbb" or a Tk grey ("gray25")."""
    text = color.strip()
    if text.startswith("#") and len(text) == 7:
        int(text[1:], 16)  # a bad digit raises ValueError
        return text.upper()
    lowered = text.lower()
    for prefix in ("gray", "grey"):
        if lowered.startswith(prefix) and lowered[len(prefix) :].isdigit():
            percent = int(lowered[len(prefix) :])
            if 0 <= percent <= 100:
                level = (percent * 255 + 50) // 100
                return f"#{level:02X}{level:02X}{level:02X}"
    raise ValueError(f"not a colour this module reads: {color!r}")


def _hls(color: str) -> tuple[float, float, float]:
    hexed = to_hex(color)
    red, green, blue = (int(hexed[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(red, green, blue)


def _from_hls(hue: float, lightness: float, saturation: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue % 1.0, lightness, saturation)
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in (red, green, blue))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def shifted(color: str, before: str, after: str) -> str:
    """`color` moved the way `before` moves to `after` (lightness, hue and saturation)."""
    hue, lightness, saturation = _hls(color)
    hue_before, light_before, sat_before = _hls(before)
    hue_after, light_after, sat_after = _hls(after)
    lightness = _clamp(lightness + light_after - light_before)
    if saturation < NEUTRAL or sat_before < NEUTRAL:
        saturation = _clamp(saturation + sat_after - sat_before)
        if sat_after >= NEUTRAL:
            hue = hue_after
    else:
        if sat_after >= NEUTRAL:
            hue = hue + hue_after - hue_before
        saturation = _clamp(saturation * sat_after / sat_before)
    return _from_hls(hue, lightness, saturation)
