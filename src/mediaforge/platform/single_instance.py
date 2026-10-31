"""One MediaForge per user. Later launches hand their files to the first one over a pipe.

Explorer starts one process per selected file, so this is what lets a right-click on 12
files open a single confirm window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from mediaforge.platform.instance_name import mutex_name, user_tag

log = logging.getLogger("mediaforge.instance")

_ERROR_ALREADY_EXISTS = 183


class InstanceMessage(BaseModel):
    paths: list[str]
    preset: str | None
    confirm: bool


def pipe_address(name: str | None = None) -> str:
    return rf"\\.\pipe\MediaForge-{name or user_tag()}"


KEY_BYTES = 32


def load_key(key_file: Path) -> bytes:
    """The per-user pipe key, created exactly once even when processes start together.

    A new key is written to a private temp file and renamed into place; the rename fails
    for everyone but the first, and they all read the winner's key.
    """
    key_file.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        try:
            data = key_file.read_bytes()
        except FileNotFoundError:
            data = b""
        except PermissionError:
            time.sleep(0.02)
            continue
        if len(data) == KEY_BYTES:
            return data
        tmp = key_file.with_name(f"{key_file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(secrets.token_bytes(KEY_BYTES))
        try:
            os.rename(tmp, key_file)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    raise OSError("Couldn't create or read the MediaForge instance key")


def _kernel32() -> Any:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


class Primary:
    def __init__(self, mutex: int, address: str, key: bytes) -> None:
        self._mutex = mutex
        self._address = address
        self._key = key
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._closing = False

    def start(self, on_message: Callable[[InstanceMessage], None]) -> None:
        self._listener = Listener(self._address, family="AF_PIPE", authkey=self._key)
        self._thread = threading.Thread(
            target=self._serve, args=(self._listener, on_message), daemon=True, name="instance"
        )
        self._thread.start()

    def _serve(self, listener: Listener, on_message: Callable[[InstanceMessage], None]) -> None:
        while not self._closing:
            try:
                conn = listener.accept()
            except Exception as exc:  # wrong auth key, broken client, or shutting down
                if self._closing:
                    return
                log.debug("Rejected a connection: %s", exc)
                continue
            with conn:
                try:
                    payload = conn.recv()
                except (EOFError, OSError):
                    continue
            if self._closing or payload is None:
                continue
            try:
                on_message(InstanceMessage.model_validate(payload))
            except ValidationError:
                log.warning("Ignored a malformed message from another MediaForge window")

    def stop_listening(self) -> None:
        """Stop taking hand-overs but keep the lock, so no second MediaForge starts while
        this one is still shutting down (and swapping in an update)."""
        self._closing = True
        if self._listener is not None:
            # Wake a blocked accept() with a bare connect-and-close. A full Client()
            # handshake could wait forever if the thread has already stopped.
            try:
                with open(self._address, "r+b", buffering=0):
                    pass
            except OSError:
                pass
            self._listener.close()
            self._listener = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def close(self) -> None:
        """Stop listening and release the lock."""
        self.stop_listening()
        if self._mutex:
            _kernel32().CloseHandle(self._mutex)
            self._mutex = 0


def try_become_primary(key_file: Path, *, name: str | None = None) -> Primary | None:
    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, mutex_name(name))
    if not handle:
        raise OSError(ctypes.get_last_error(), "Couldn't create the MediaForge mutex")
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        return None
    return Primary(handle, pipe_address(name), load_key(key_file))


def send_to_primary(
    msg: InstanceMessage, key_file: Path, timeout_s: float = 15.0, *, name: str | None = None
) -> bool:
    key = load_key(key_file)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with Client(pipe_address(name), family="AF_PIPE", authkey=key) as conn:
                conn.send(msg.model_dump(mode="json"))
            return True
        except Exception:  # not listening yet, busy, or the key doesn't match
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


class Inbox:
    """Holds handed-over launches until the window is ready to take them.

    The listener starts as early as possible so other processes can hand over and exit
    quickly; messages that arrive while the app is still loading wait here.
    """

    def __init__(self) -> None:
        self._waiting: list[InstanceMessage] = []
        self._handler: Callable[[InstanceMessage], None] | None = None

    def deliver(self, msg: InstanceMessage) -> None:
        if self._handler is None:
            self._waiting.append(msg)
        else:
            self._handler(msg)

    def attach(self, handler: Callable[[InstanceMessage], None]) -> None:
        self._handler = handler
        waiting, self._waiting = self._waiting, []
        for msg in waiting:
            handler(msg)


class ConfirmBatcher:
    """Collect right-click paths until no more arrive for quiet_s, then hand them over."""

    def __init__(
        self, quiet_s: float = 0.4, *, on_batch: Callable[[str | None, list[Path]], None]
    ) -> None:
        self._quiet = quiet_s
        self._on_batch = on_batch
        self._pending: dict[str | None, list[Path]] = {}
        self._last = 0.0
        self._lock = threading.Lock()

    def add(self, msg: InstanceMessage, now: float | None = None) -> None:
        with self._lock:
            self._pending.setdefault(msg.preset, []).extend(Path(p) for p in msg.paths)
            self._last = time.monotonic() if now is None else now

    def flush_due(self, now: float | None = None) -> None:
        with self._lock:
            current = time.monotonic() if now is None else now
            if not self._pending or current - self._last < self._quiet:
                return
            batches, self._pending = self._pending, {}
        for preset, paths in batches.items():
            self._on_batch(preset, paths)
