"""The names of MediaForge's per-user single-instance lock and pipe, and a quick look at the
lock. Standard library only: the launcher asks before any package is imported."""

from __future__ import annotations

import ctypes
import getpass
import re
import sys

_SYNCHRONIZE = 0x00100000


def user_tag() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser()) or "user"


def mutex_name(name: str | None = None) -> str:
    return f"Local\\MediaForge-{name or user_tag()}"


def instance_running(name: str | None = None) -> bool:
    """True while another MediaForge holds the lock (it may already be shutting down)."""
    if sys.platform != "win32":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p
    k32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenMutexW(_SYNCHRONIZE, False, mutex_name(name))
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True
