"""Start with Windows: a shortcut in the user's Startup folder that opens MediaForge in the tray.

A port of AirGrabber's app/services/startup.py. The shortcut mirrors install.ps1's New-Shortcut
(pythonw.exe, "<install>\\main.py", the install folder as working folder, MediaForge's icon and
AppUserModelID) plus `--tray`. It is written by PowerShell's WScript.Shell (no pywin32) with the
helper shortcut_app_id.cs for the AppUserModelID, as install.ps1 does; turning it off deletes the
file. Current user only, no administrator permission. The Startup folder and the writer are
injectable, so tests never touch the real ones.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from mediaforge.platform.identity import APP_USER_MODEL_ID
from mediaforge.platform.update_files import is_development_copy

log = logging.getLogger("mediaforge.startup")

INSTALL_DIR = Path(__file__).resolve().parents[3]  # <install>\src\mediaforge\platform
SHORTCUT_NAME = "MediaForge.lnk"
STARTUP_SUBDIR = ("Microsoft", "Windows", "Start Menu", "Programs", "Startup")  # in %APPDATA%
ICON_PATH = r"src\mediaforge\assets\MediaForge.ico"  # as install.ps1's $IconPath
TRAY_OPTION = "--tray"  # start with only the tray icon showing
DESCRIPTION = "Clean names for your shows and movies"
HELPER = Path(__file__).resolve().parents[1] / "shortcut_app_id.cs"
TIMEOUT_S = 30.0
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ENV = "MEDIAFORGE_LNK_"  # values arrive as environment variables: nothing needs quoting
_SCRIPT = "; ".join(
    (
        "$ErrorActionPreference = 'Stop'",
        f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:{_ENV}PATH)",
        f"$link.TargetPath = $env:{_ENV}TARGET",
        f"$link.Arguments = $env:{_ENV}ARGUMENTS",
        f"$link.WorkingDirectory = $env:{_ENV}WORKING_DIR",
        f"$link.IconLocation = $env:{_ENV}ICON",
        f"$link.Description = $env:{_ENV}DESCRIPTION",
        "$link.Save()",
        # Optional: MediaForge sets its own AppUserModelID when it starts.
        "try { if (-not ('MediaForge.ShortcutAppId' -as [type])) "
        f"{{ Add-Type -TypeDefinition ([IO.File]::ReadAllText($env:{_ENV}HELPER)) }}; "
        f"[MediaForge.ShortcutAppId]::Set($env:{_ENV}PATH, $env:{_ENV}APP_ID) }} "
        "catch { [Console]::Error.WriteLine('AppUserModelID not set: ' + $_.Exception.Message) }",
    )
)
ADD_FAILED = "MediaForge could not be added to Windows startup."
REMOVE_FAILED = "MediaForge could not be removed from Windows startup."


class StartupError(Exception):
    """The shortcut could not be added or removed; the message is fit for the window."""


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """Everything the .lnk holds, as Windows path strings."""

    path: str  # the .lnk itself
    target: str  # pythonw.exe
    arguments: str
    working_dir: str
    icon: str  # "<file>,<index>"
    description: str
    app_id: str  # System.AppUserModel.ID


type ShortcutWriter = Callable[[ShortcutSpec], Awaitable[None]]


def shortcut_spec(install_dir: Path, python: Path, startup_dir: Path) -> ShortcutSpec:
    """The Startup shortcut for the copy in `install_dir`, run by the pythonw.exe beside `python`.
    Pure: nothing is looked up on disk."""
    root = PureWindowsPath(install_dir)
    return ShortcutSpec(
        path=str(PureWindowsPath(startup_dir, SHORTCUT_NAME)),
        target=str(PureWindowsPath(python).with_name("pythonw.exe")),
        arguments=f'"{root / "main.py"}" {TRAY_OPTION}',
        working_dir=str(root),
        icon=f"{root / ICON_PATH},0",
        description=DESCRIPTION,
        app_id=APP_USER_MODEL_ID,
    )


def default_startup_dir() -> Path | None:
    """%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup; None outside Windows."""
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    return Path(appdata, *STARTUP_SUBDIR) if appdata else None


def is_enabled(*, startup_dir: Path | None = None) -> bool:
    """Whether MediaForge's shortcut is in the Startup folder."""
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    return folder is not None and (folder / SHORTCUT_NAME).is_file()


async def set_enabled(
    on: bool,
    *,
    startup_dir: Path | None = None,
    install_dir: Path = INSTALL_DIR,
    python: Path | None = None,
    write: ShortcutWriter | None = None,
) -> None:
    """Add (or rewrite) the Startup shortcut, or remove it. Raises StartupError."""
    folder = startup_dir if startup_dir is not None else default_startup_dir()
    if folder is None:
        return
    link = folder / SHORTCUT_NAME
    if not on:
        try:
            link.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Couldn't remove the Startup shortcut: %s", exc)
            raise StartupError(REMOVE_FAILED) from exc
        log.info("Start with Windows: off")
        return
    if is_development_copy(install_dir):
        raise StartupError("Only an installed MediaForge can start with Windows.")
    python = python if python is not None else Path(sys.executable)
    spec = shortcut_spec(install_dir, python, folder)
    if not await asyncio.to_thread(Path(spec.target).is_file):  # as install.ps1: else python.exe
        spec = dataclasses.replace(spec, target=str(python))
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("Couldn't create the Startup folder: %s", exc)
        raise StartupError(ADD_FAILED) from exc
    await (write or write_shortcut)(spec)
    log.info("Start with Windows: on")


async def write_shortcut(spec: ShortcutSpec) -> None:
    """Create or overwrite the .lnk with Windows PowerShell 5.1. Raises StartupError."""
    env = {
        **os.environ,
        f"{_ENV}PATH": spec.path,
        f"{_ENV}TARGET": spec.target,
        f"{_ENV}ARGUMENTS": spec.arguments,
        f"{_ENV}WORKING_DIR": spec.working_dir,
        f"{_ENV}ICON": spec.icon,
        f"{_ENV}DESCRIPTION": spec.description,
        f"{_ENV}APP_ID": spec.app_id,
        f"{_ENV}HELPER": str(HELPER),
    }
    command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            "-Command",
            _SCRIPT,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        log.warning("Couldn't start PowerShell for the Startup shortcut: %s", exc)
        raise StartupError(ADD_FAILED) from exc
    try:
        async with asyncio.timeout(TIMEOUT_S):
            _, stderr = await process.communicate()
    except TimeoutError:
        process.kill()
        log.warning("PowerShell took too long writing the Startup shortcut")
        raise StartupError(ADD_FAILED) from None
    detail = stderr.decode(errors="replace").strip()[-500:]
    if process.returncode != 0:
        log.warning("PowerShell couldn't write the Startup shortcut: %s", detail)
        raise StartupError(ADD_FAILED)
    if detail:
        log.warning("The Startup shortcut was written, but: %s", detail)


def _powershell() -> str:
    """Windows PowerShell 5.1, which every Windows 10/11 has (as install.ps1 is written for)."""
    root = os.environ.get("SystemRoot")
    if root:
        exe = Path(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if exe.is_file():
            return str(exe)
    return "powershell.exe"
