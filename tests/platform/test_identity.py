"""MediaForge's own identity in Windows (AppUserModelID).

Without it Windows files MediaForge under the shared pythonw.exe, so its taskbar button took the
name and icon of another program's shortcut that starts the same pythonw.exe (AirGrabber).
"""

import subprocess
import sys

import pytest

from mediaforge.platform.identity import APP_USER_MODEL_ID

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_the_id_is_mediaforges_own() -> None:
    assert APP_USER_MODEL_ID == "drunkgummyboy.MediaForge"


def test_set_process_app_id_gives_the_process_the_id() -> None:
    script = (
        "import ctypes\n"
        "from ctypes import wintypes\n"
        "from mediaforge.platform.identity import set_process_app_id\n"
        "assert set_process_app_id()\n"
        "shell32 = ctypes.WinDLL('shell32')\n"
        "out = wintypes.LPWSTR()\n"
        "shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(out))\n"
        "print(out.value)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.stdout.strip() == APP_USER_MODEL_ID, result.stderr
