"""Unpack a GitHub branch archive and swap its app files into an installed copy.

An installed copy (made by install.ps1) holds exactly APP_ENTRIES next to its private `.venv`;
an update replaces exactly those entries, so the venv and anything else in the folder stay.
Settings and history live in the data folders (platform/paths.py), never next to the code.

Two ways in, as in AirGrabber:
- `install_archive` swaps the new files in at once (Settings' "Update to …"; a restart follows).
- `stage_archive` only unpacks and validates into `<install>/.update/new`; the launcher swaps
  them in with `apply_staged` once MediaForge has shut down.

Standard library only: the launcher imports this after shutdown, and it must work even when an
update brought packages that aren't installed yet.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import shutil
import stat
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

log = logging.getLogger("mediaforge.update")

APP_ENTRIES = ("main.py", "pyproject.toml", "src")  # keep in step with install.ps1
# The swap order: the src folder first; the files are then replaced in place (never missing),
# main.py last, because main.py is what repairs a swap that was cut off (`restore_interrupted`).
_SWAP_ORDER = ("src", "pyproject.toml", "main.py")
VERSION_FILE = ("src", "mediaforge", "__init__.py")
STAGING_DIR = ".update"
READY_FILE = "ready"  # in STAGING_DIR: written last by stage_archive (holds the staged version)

_VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)
_RELEASE = re.compile(r"\d+(?:\.\d+)*")
_RENAME_ATTEMPTS = 5  # antivirus scanners briefly lock freshly written files on Windows
# Staging and installing run in worker threads, applying at exit: one at a time.
_STAGING_LOCK = threading.Lock()
_STAGING_WAIT_S = 120.0


class UpdateFileError(Exception):
    """An archive that can't be used, or files that couldn't be swapped; plain-English message."""


def version_tuple(version: str) -> tuple[int, ...] | None:
    """Release numbers with trailing zeros dropped: '2.14.0b1' -> (2, 14), '10.0' -> (10,)."""
    match = _RELEASE.match(version.strip())
    if match is None:
        return None
    parts = [int(part) for part in match[0].split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def parse_version(source: str) -> str | None:
    """`__version__` from the text of src/mediaforge/__init__.py."""
    match = _VERSION_LINE.search(source)
    return match[1] if match else None


def is_newer(latest: str, current: str) -> bool:
    new, old = version_tuple(latest), version_tuple(current)
    return new is not None and old is not None and new > old


def is_development_copy(install_dir: Path) -> bool:
    """The source folder (it has the tests, or is a git checkout) is never overwritten."""
    return (install_dir / "tests").is_dir() or (install_dir / ".git").exists()


def install_archive(data: bytes, install_dir: Path) -> str:
    """Unpack an archive and swap its APP_ENTRIES in. Returns the new version.

    The old files are moved aside first and put back if anything fails half-way.
    """
    with _STAGING_LOCK:
        version = _unpack(data, install_dir)
        staging = install_dir / STAGING_DIR
        _swap(staging / "new", staging / "old", install_dir)
        _remove_tree(staging)  # best effort: a leftover is cleared by the next update
        return version


def stage_archive(data: bytes, install_dir: Path, *, newer_than: str | None = None) -> str:
    """Unpack and validate an archive into `<install>/.update/new` without swapping it in.

    With `newer_than`, an archive that is not newer than that version is refused. The ready
    marker is written last, so an unpack that was cut short is never applied.
    """
    with _STAGING_LOCK:
        version = _unpack(data, install_dir)
        staging = install_dir / STAGING_DIR
        try:
            if newer_than is not None and not is_newer(version, newer_than):
                raise UpdateFileError(
                    f"The download is version {version}, which is not newer than {newer_than}."
                )
            (staging / READY_FILE).write_text(version, encoding="utf-8")
        except BaseException:
            _remove_tree(staging)
            raise
        return version


def apply_staged(install_dir: Path) -> str | None:
    """Swap in the update `stage_archive` left in `<install>/.update`.

    Returns the version installed, or None when nothing was installed. Never raises: a failed
    swap is rolled back and logged. A staged update that is incomplete, or not newer than the
    version on disk (the installer ran in between), is deleted instead. A development copy is
    never touched.
    """
    try:
        if is_development_copy(install_dir):
            return None
        if not _STAGING_LOCK.acquire(timeout=_STAGING_WAIT_S):
            log.warning("The update was still being unpacked; it is installed next time")
            return None
        try:
            return _apply_staged(install_dir)
        finally:
            _STAGING_LOCK.release()
    except Exception:
        log.exception("Installing the downloaded update failed")
        return None


def installed_version(install_dir: Path) -> str | None:
    return _read_version(install_dir.joinpath(*VERSION_FILE))


def restore_interrupted(install_dir: Path) -> bool:
    """Repair a swap that was cut off (power cut, killed process): put the old version back in
    full from `.update/old`, so all app entries belong to one version again. A swap that had
    finished and only missed its clean-up keeps the new version. True when files were restored.

    main.py calls this at every start, before importing anything else. Never raises.
    """
    staging = install_dir / STAGING_DIR
    old, new = staging / "old", staging / "new"
    try:
        backed_up = [entry for entry in APP_ENTRIES if (old / entry).exists()]
        if not backed_up:
            return False
        unfinished = any((new / entry).exists() for entry in APP_ENTRIES)
        if not unfinished and all((install_dir / entry).exists() for entry in APP_ENTRIES):
            _remove_tree(staging)  # the swap finished; only its clean-up was cut short
            return False
        for entry in backed_up:
            target = install_dir / entry
            if (old / entry).is_dir():
                if target.exists():
                    _remove(target)  # a half-swapped or new copy
                _rename(old / entry, target)
            else:
                _replace(old / entry, target)
        _remove_tree(staging)  # the update is downloaded again next time
        log.warning("An update was cut off; the previous version was put back")
        return True
    except Exception:
        log.exception("Could not repair an update that was cut off")
        return False


def _apply_staged(install_dir: Path) -> str | None:
    staging = install_dir / STAGING_DIR
    restore_interrupted(install_dir)  # never delete the only copy of the old files
    if not staging.exists():
        return None
    staged = _staged_version(staging)
    if staged is None:
        log.info("Removing an incomplete downloaded update")
        _remove_tree(staging)
        return None
    installed = installed_version(install_dir)
    if installed is None or not is_newer(staged, installed):
        log.info("Removing the downloaded update %s: %s is installed", staged, installed or "?")
        _remove_tree(staging)
        return None
    old = staging / "old"
    try:
        _swap(staging / "new", old, install_dir)
    except UpdateFileError as exc:
        log.warning("Could not install the downloaded update %s: %s", staged, exc)
        (staging / READY_FILE).unlink(missing_ok=True)  # never tried again
        if not old.exists() or not any(old.iterdir()):  # rolled back: no old file is left there
            _remove_tree(staging)
        return None
    _remove_tree(staging)  # best effort: a leftover is cleared by the next update
    log.info("Installed the downloaded update: %s -> %s", installed, staged)
    return staged


def _unpack(data: bytes, install_dir: Path) -> str:
    """Extract and validate an archive into `<install>/.update/new` (cleared first).

    Returns its version. On failure the staging folder is removed again and nothing else changed.
    """
    staging = install_dir / STAGING_DIR
    restore_interrupted(install_dir)  # never delete the only copy of the old files
    _remove_tree(staging)
    if staging.exists():
        raise UpdateFileError(f"Could not clear the folder {staging}; delete it and try again.")
    new = staging / "new"
    try:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                _extract(archive, new)
        except zipfile.BadZipFile as exc:
            raise UpdateFileError("The download is not a valid archive.") from exc
        for entry in APP_ENTRIES:
            if not (new / entry).exists():
                raise UpdateFileError(f"The download has no {entry}; nothing was changed.")
        version = installed_version(new)
        if version is None:
            raise UpdateFileError("The download has no version number; nothing was changed.")
    except BaseException:
        _remove_tree(staging)
        raise
    return version


def _staged_version(staging: Path) -> str | None:
    """The version of a complete staged update (ready marker written, every entry there, the
    marker's version is the files' version), else None."""
    try:
        marker = (staging / READY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    new = staging / "new"
    if not all((new / entry).exists() for entry in APP_ENTRIES):
        return None
    version = installed_version(new)
    return version if version is not None and version == marker else None


def _read_version(init_file: Path) -> str | None:
    try:
        return parse_version(init_file.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _extract(archive: zipfile.ZipFile, target: Path) -> None:
    """The APP_ENTRIES part of the archive, without its top folder ("Media-Forge-main/")."""
    names = [info.filename for info in archive.infolist()]
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) != 1:
        raise UpdateFileError("The download has an unexpected layout; nothing was changed.")
    root = target.resolve()
    for info in archive.infolist():
        parts = PurePosixPath(info.filename).parts[1:]
        if not parts or parts[0] not in APP_ENTRIES or "__pycache__" in parts:
            continue
        destination = root.joinpath(*parts).resolve()
        if not destination.is_relative_to(root) or ".." in parts:
            raise UpdateFileError("The download contains unsafe paths; nothing was changed.")
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("wb") as out:
            shutil.copyfileobj(source, out)


def _swap(new: Path, old: Path, install_dir: Path) -> None:
    """Put the new entries in place, keeping the old ones in `old` until it has worked."""
    old.mkdir(parents=True, exist_ok=True)
    started: list[str] = []
    try:
        for entry in _SWAP_ORDER:
            target = install_dir / entry
            started.append(entry)
            if (new / entry).is_dir():
                if target.exists():
                    _rename(target, old / entry)
                _rename(new / entry, target)
            else:
                if target.exists():
                    _backup(target, old / entry)
                _replace(new / entry, target)  # a file is never missing, not even for a moment
    except OSError as exc:
        log.warning("Update could not replace the app files; rolling back: %r", exc)
        try:
            for entry in reversed(started):
                target = install_dir / entry
                if not (old / entry).exists():
                    continue
                if (old / entry).is_dir():
                    if not (new / entry).exists() and target.exists():  # the new copy made it in
                        _remove(target)
                    _rename(old / entry, target)
                else:
                    _replace(old / entry, target)
        except OSError as rollback:
            log.error("Rolling back the update failed: %r", rollback)
            raise UpdateFileError(
                "The update failed half-way and could not be undone. "
                "Reinstall MediaForge with the install command."
            ) from exc
        raise UpdateFileError(
            "Could not replace the app files (is another program using them?). Nothing was changed."
        ) from exc


def _backup(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)


def _replace(source: Path, destination: Path) -> None:
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _rename(source: Path, destination: Path) -> None:
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.rename(source, destination)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, onexc=_clear_read_only)
    else:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _remove_tree(path: Path) -> None:
    with contextlib.suppress(OSError):
        shutil.rmtree(path, onexc=_clear_read_only)


def _clear_read_only(function: Callable[[str], object], path: str, _exc: BaseException) -> None:
    """rmtree helper: Windows refuses to delete read-only files and folders."""
    os.chmod(path, stat.S_IWRITE)
    function(path)
