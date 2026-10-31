"""AirGrabber's family colours: Slate by default, ten accents, light and dark, all readable."""

import pytest

from mediaforge.ui.theme import ACCENTS, DARK, LIGHT, Palette, accent_label, palette_for


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_slate_matches_airgrabber() -> None:
    assert (DARK.main, DARK.surface, DARK.accent) == ("#161616", "#242424", "#4E6A85")
    assert (LIGHT.main, LIGHT.surface, LIGHT.accent) == ("#F2F4F7", "#FFFFFF", "#4E6A85")
    assert DARK.text == "#FFFFFF" and DARK.muted == "#A4B2C6"


def test_there_are_ten_accents_slate_first() -> None:
    assert ACCENTS[0] == "slate" and len(ACCENTS) == 10
    assert accent_label("petrol") == "Petrol"


@pytest.mark.parametrize("accent", ACCENTS)
@pytest.mark.parametrize("theme", ["Dark", "Light"])
def test_every_accent_builds_both_looks(theme: str, accent: str) -> None:
    palette = palette_for(theme, accent)
    assert isinstance(palette, Palette)
    for value in vars(palette).values():
        if value != palette.name:
            assert isinstance(value, str) and value.startswith("#") and len(value) == 7, value


@pytest.mark.parametrize("accent", ACCENTS)
@pytest.mark.parametrize("theme", ["Dark", "Light"])
def test_text_is_readable_everywhere(theme: str, accent: str) -> None:
    # Review focus 1: light mode and every accent, not just dark Slate.
    p = palette_for(theme, accent)
    for background in (p.main, p.surface, p.raised, p.rail):
        assert contrast(p.text, background) >= 4.5, (theme, accent, background)
        assert contrast(p.muted, background) >= 3, (theme, accent, background)
    assert contrast(p.accent_text, p.accent) >= 3, (theme, accent)
    assert contrast(p.accent_text, p.accent_hover) >= 3, (theme, accent)


def test_unknown_accent_falls_back_to_slate() -> None:
    assert palette_for("Dark", "neon") == palette_for("Dark", "slate") == DARK
