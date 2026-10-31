"""Watched download folders: which videos are there, and which have really finished downloading.

A file counts as finished only when it looked the same (size and time) at two looks in a row,
nothing has written to it for QUIET_S, and it can be opened with nobody else holding it. Change
notifications are not used: over SMB (the NAS) Windows misses them, so a look every few minutes is
the base. Temporary download names, samples and the NAS's own folders are never candidates.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from mediaforge.core import fsops
from mediaforge.core.parser import is_video

LOOK_INTERVAL_S = 300  # a look every 5 minutes
QUIET_S = 600  # untouched for 10 minutes
TEMP_SUFFIXES = (".part", ".!qb", ".crdownload", ".tmp", ".partial", ".download", ".aria2")
SKIP_FOLDERS = frozenset({"@eadir", "#recycle", "#snapshot", "$recycle.bin"})
# "sample" as a word of its own: "-sample", ".Sample.", "_sample" (not "Samples.Of.Life").
_SAMPLE = re.compile(r"(?<![a-z0-9])sample(?![a-z0-9])", re.IGNORECASE)

_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def is_sample(path: Path) -> bool:
    return _SAMPLE.search(path.stem) is not None


def _skip_folder(name: str) -> bool:
    return name.lower() in SKIP_FOLDERS or name.startswith(".")


def candidates(root: Path) -> list[Path]:
    """The videos under `root` that may be downloads (blocking: run it in a thread).

    Raises OSError when `root` itself can't be read (not there, the NAS is off).
    """
    with os.scandir(fsops.long_path(root)):
        pass
    found: list[Path] = []
    for folder, dirs, names in os.walk(fsops.long_path(root)):
        dirs[:] = [d for d in dirs if not _skip_folder(d)]
        lower = {n.lower() for n in names}
        for name in names:
            path = Path(name)
            if name.startswith(("~", ".")) or not is_video(path) or is_sample(path):
                continue
            if any(f"{name}{suffix}".lower() in lower for suffix in TEMP_SUFFIXES):
                continue  # the downloader keeps a marker beside a file it is still writing
            found.append(fsops.short_path(os.path.join(folder, name)))
    return sorted(found, key=lambda p: str(p).casefold())


def exclusive_open(path: Path) -> bool:
    """Whether `path` can be opened with nobody else reading or writing it."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.restype = ctypes.c_void_p
    handle = create(fsops.long_path(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == _INVALID_HANDLE:
        return False
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return True


class Watcher:
    """Remembers what each file looked like at the last look."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        stat: Callable[[Path], tuple[int, int]] = fsops.stamp,
        can_open: Callable[[Path], bool] = exclusive_open,
    ) -> None:
        self._now = now
        self._stat = stat
        self._can_open = can_open
        self._seen: dict[str, tuple[int, int]] = {}

    def finished(self, files: Sequence[Path]) -> list[Path]:
        """The files among this look's `files` that have finished downloading."""
        now = self._now()
        seen: dict[str, tuple[int, int]] = {}
        done: list[Path] = []
        for file in files:
            key = os.path.normcase(str(file))
            try:
                stamp = self._stat(file)
            except OSError:
                continue  # gone between listing and now: forgotten
            seen[key] = stamp
            if self._seen.get(key) != stamp:
                continue  # new, or still changing
            quiet = now - stamp[1] / 1_000_000_000 >= QUIET_S
            if quiet and self._can_open(file):
                done.append(file)
        self._seen = seen
        return done
