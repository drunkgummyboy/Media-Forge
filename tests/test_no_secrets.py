"""What goes on the public GitHub page: never the TMDB key or the old app's settings files,
and nothing left of the PyInstaller build. Keys are compared, never printed."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ("mediaforge_config.json", "mediaforge_undo.json")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [name for name in out.split("\0") if name]


def local_key() -> str:
    try:
        data = json.loads((ROOT / "mediaforge_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    return str(data.get("TMDB_API_KEY") or "").strip()


def test_config_and_undo_files_are_never_tracked() -> None:
    names = {Path(name).name for name in tracked_files()}
    assert not names & set(PRIVATE)


def test_no_tracked_file_contains_the_tmdb_key() -> None:
    key = local_key()
    if len(key) < 16:
        pytest.skip("no local TMDB key to look for")
    needle = key.encode()
    found = [name for name in tracked_files() if needle in (ROOT / name).read_bytes()]
    assert found == [], "the TMDB key is in a tracked file"  # names only, never the key


def test_nothing_frozen_left() -> None:
    offenders: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ('getattr(sys, "frozen"', "sys.frozen", "_MEIPASS", "PyInstaller"):
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}: {marker}")
    assert offenders == []
    assert not (ROOT / "release").exists()


def test_assets_ship_inside_the_package() -> None:
    # Installed copies get only main.py, pyproject.toml and src/: the icon and logo live in src.
    assets = ROOT / "src" / "mediaforge" / "assets"
    assert (assets / "MediaForge.ico").stat().st_size > 1000
    assert (assets / "MediaForge.png").stat().st_size > 1000
    # Old single-file copies download their icon from the page's top level.
    assert (ROOT / "MediaForge.ico").is_file() and (ROOT / "MediaForge.png").is_file()
