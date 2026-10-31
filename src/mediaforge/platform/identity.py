"""MediaForge's own identity in Windows: its AppUserModelID.

Without one, Windows files MediaForge under the shared pythonw.exe it runs on: its taskbar button
then takes the name and icon of any other program's shortcut that starts the same pythonw.exe
(the owner's AirGrabber, for one). The launcher gives the process the ID before any window exists,
and install.ps1 writes the same ID into MediaForge's shortcuts (src/mediaforge/shortcut_app_id.cs).
Ported from AirGrabber's app/identity.py.

Standard library only: the launcher uses this before the package check. A no-op off Windows.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("mediaforge.identity")

APP_USER_MODEL_ID = "drunkgummyboy.MediaForge"  # keep in step with install.ps1's $AppId


def set_process_app_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Give this process `app_id`. Call it before the first window exists.

    False (logged) when Windows refuses; always False outside Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32")
        setter = shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = (wintypes.LPCWSTR,)
        setter.restype = ctypes.c_long  # HRESULT
        result = int(setter(app_id))
    except (OSError, AttributeError) as exc:
        log.warning("Could not set the AppUserModelID: %r", exc)
        return False
    if result < 0:
        log.warning("Could not set the AppUserModelID (HRESULT 0x%08X)", result & 0xFFFFFFFF)
        return False
    return True
