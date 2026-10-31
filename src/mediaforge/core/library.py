"""The library folders: what is still missing before MediaForge can watch, and folder tests.

Paths are compared the way Windows does (case-insensitive, `\\\\server\\share` included) and by
whole folder names, so `Series2` is not inside `Series`.
"""

from __future__ import annotations

import os
from pathlib import Path

from mediaforge.db.settings import AppSettings

OVERLAP = "A watched folder can't be inside a library folder, or the other way round."


def _parts(path: Path) -> tuple[str, ...]:
    return tuple(os.path.normcase(part).rstrip("\\/") for part in path.parts)


def inside(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` or anything below it."""
    mine, theirs = _parts(path), _parts(folder)
    return len(mine) >= len(theirs) and mine[: len(theirs)] == theirs


def library_problems(settings: AppSettings) -> list[str]:
    """What still needs choosing (plain sentences); empty when watching can start."""
    problems: list[str] = []
    if not settings.watch_folders:
        problems.append("Choose a download folder to watch.")
    if not settings.series_folder:
        problems.append("Choose the Series library folder.")
    if not settings.movies_folder:
        problems.append("Choose the Movies library folder.")
    libraries = [Path(f) for f in (settings.series_folder, settings.movies_folder) if f]
    for watched in map(Path, settings.watch_folders):
        if any(inside(watched, lib) or inside(lib, watched) for lib in libraries):
            problems.append(OVERLAP)
            break
    return problems


def watched_root(path: Path, settings: AppSettings) -> Path | None:
    """The watched folder `path` lies in, if any."""
    return next((Path(f) for f in settings.watch_folders if inside(path, Path(f))), None)
