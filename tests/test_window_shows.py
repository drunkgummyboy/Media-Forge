"""A real start: MediaForge's window must appear, also when the welcome window doesn't.

Regression: customtkinter hides a new main window on Windows until its own update() or
mainloop() runs, and the async loop calls neither, so a second start showed nothing at all.
"""

import asyncio
import ctypes
import ctypes.wintypes as wt
import os
import site
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from mediaforge.db.database import Database
from mediaforge.db.settings import SettingsRepo
from mediaforge.platform.paths import app_paths

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows windows")


def visible_tk_windows(pid: int) -> list[str]:
    user32 = ctypes.WinDLL("user32")
    found: list[str] = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def check(hwnd: int, _: int) -> bool:
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            title = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, title, 64)
            if name.value == "TkTopLevel":
                found.append(title.value)
        return True

    user32.EnumWindows(proc(check), 0)
    return found


async def _seen_before() -> None:
    """Settings of a copy that has started before: no welcome window this time."""
    paths = app_paths()  # MEDIAFORGE_HOME: a temp folder (autouse fixture)
    paths.ensure()
    db = await Database.open(paths.db_file)
    settings = SettingsRepo(db)
    await settings.save(
        (await settings.load()).model_copy(
            update={"first_start_done": True, "legacy_imported": True}
        )
    )
    await db.close()


def test_second_start_shows_the_main_window(tmp_path: Path) -> None:
    asyncio.run(_seen_before())
    env = dict(os.environ)
    for name in ("LOGNAME", "USER", "LNAME"):
        env.pop(name, None)
    env["USERNAME"] = f"mftest{uuid.uuid4().hex[:8]}"  # its own lock, apart from a real copy
    # The real interpreter, not the venv's redirector, so the window belongs to this process.
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), *site.getsitepackages()])
    exe = getattr(sys, "_base_executable", sys.executable)
    proc = subprocess.Popen([exe, "-m", "mediaforge"], cwd=tmp_path, env=env)
    try:
        deadline = time.monotonic() + 8  # well before the update check at 10 s
        shown: list[str] = []
        while time.monotonic() < deadline and not shown:
            time.sleep(0.25)
            assert proc.poll() is None, "MediaForge exited"
            shown = visible_tk_windows(proc.pid)
        assert shown == ["MediaForge"]
    finally:
        proc.terminate()
        proc.wait(10)
