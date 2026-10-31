"""Every screen takes its colours from the palette, so light, dark and all accents just work."""

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "src" / "mediaforge" / "ui"
# Where colours are defined, and the package-check window (plain Tk, before the palette exists).
ALLOWED = {"theme.py", "palettes.py", "bootstrap_window.py"}
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def test_ui_uses_the_palette() -> None:
    found = [
        f"{path.relative_to(UI)}:{number}: {line.strip()}"
        for path in sorted(UI.rglob("*.py"))
        if path.name not in ALLOWED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if HEX.search(line.split("#", 1)[0] + "#" + line.split("#", 1)[1] if "#" in line else line)
        and not line.lstrip().startswith("#")
    ]
    assert found == []


def test_text_is_never_in_the_plain_accent() -> None:
    """The accent is a fill; coloured text uses `link`, its readable shade in both looks."""
    text_in_accent = re.compile(r"(text_color|fill)=[^,]*\.accent\b(?!_)")
    found = [
        f"{path.relative_to(UI)}:{number}: {line.strip()}"
        for path in sorted(UI.rglob("*.py"))
        if path.name not in ALLOWED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if text_in_accent.search(line) and ("create_text" in line or "text_color" in line)
    ]
    assert found == []
