"""The tray icon: MediaForge keeps watching the download folders with its window closed (1.3).

A port of AirGrabber's app/ui/tray.py. pystray needs a Win32 message loop of its own, so it runs
on a daemon thread: the one exception to "no threads" (CLAUDE.md). Its menu callbacks only hand
work to the asyncio loop with `loop.call_soon_threadsafe`; nothing on that thread touches Tk.
Closing the window hides it in the tray while renaming on its own is Trial or On (`close_action`);
without a working tray icon, or with it Off, closing quits as before.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("mediaforge.tray")

APP_NAME = "MediaForge"
_STOP_TIMEOUT_S = 3.0
_BADGE = (231, 76, 60, 255)  # the red of the Inbox badge (a PIL image, not a Tk widget)


def close_action(*, automatic: str, tray_running: bool) -> Literal["hide", "quit"]:
    """What closing the window does: hide it in the tray while watching, otherwise quit."""
    return "hide" if tray_running and automatic != "off" else "quit"


def tooltip(count: int) -> str:
    if count <= 0:
        return APP_NAME
    return f"{APP_NAME} · {count} file{'s need' if count != 1 else ' needs'} you"


def with_badge(image: Image.Image, count: int) -> Image.Image:
    """The icon with a small red count in its corner."""
    badged = image.convert("RGBA").copy()
    size = badged.width
    radius = size // 4
    draw = ImageDraw.Draw(badged)
    box = (size - 2 * radius, size - 2 * radius, size - 1, size - 1)
    draw.ellipse(box, fill=_BADGE)
    text = str(count) if count < 10 else "9+"
    try:
        face: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.truetype(
            "segoeuib.ttf", int(radius * 1.3)
        )
    except OSError:
        face = ImageFont.load_default()
    centre = (size - radius, size - radius)
    draw.text(centre, text, fill=(255, 255, 255, 255), font=face, anchor="mm")
    return badged


class TrayIcon:
    """The tray icon and its menu: Open MediaForge (default: clicking the icon), Check downloads
    now, Quit.

    Every callback runs on `loop`'s thread (handed over with call_soon_threadsafe), never on
    pystray's. `start` raises ImportError when pystray is missing; `on_failed` is called (on the
    loop) when its message loop dies later. `backend` stands in for the pystray module in tests.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        image: Image.Image,
        *,
        on_open: Callable[[], None],
        on_check_now: Callable[[], None],
        on_quit: Callable[[], None],
        on_failed: Callable[[], None] | None = None,
        backend: Any = None,
    ) -> None:
        self._loop = loop
        self._image = image
        self._on_open = on_open
        self._on_check_now = on_check_now
        self._on_quit = on_quit
        self._on_failed = on_failed
        self._backend = backend
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # guards the three flags below (two threads use them)
        self._ready = False  # pystray's loop runs and the icon was shown
        self._stopping = False
        self._failed = False
        self._count = 0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and not self._stopping and not self._failed

    def start(self) -> None:
        if self._thread is not None or self._stopping:
            return
        pystray = self._backend if self._backend is not None else _import_pystray()
        menu = pystray.Menu(
            pystray.MenuItem("Open MediaForge", self._menu_action(self._on_open), default=True),
            pystray.MenuItem("Check downloads now", self._menu_action(self._on_check_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._menu_action(self._on_quit)),
        )
        self._icon = pystray.Icon(APP_NAME, self._image, APP_NAME, menu)
        # A daemon thread: pystray's own setup thread inherits that, so neither can keep the
        # process alive if stop() is never reached.
        thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Remove the icon and end its thread (waits a few seconds). Never touches Tk or the
        loop, so it may be called after the loop has closed. Safe to call twice."""
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            ready = self._ready
        thread = self._thread
        if thread is None:
            return
        if ready:
            self._icon.stop()
        if thread is not threading.current_thread():
            thread.join(_STOP_TIMEOUT_S)
            if thread.is_alive():
                log.warning("The tray icon did not stop in time")

    def set_count(self, count: int) -> None:
        """How many downloads need the owner: in the tooltip, and as a badge on the icon."""
        if count == self._count or self._icon is None:
            return
        self._count = count
        try:
            self._icon.title = tooltip(count)
            self._icon.icon = with_badge(self._image, count) if count > 0 else self._image
        except Exception:
            log.debug("Couldn't update the tray icon", exc_info=True)

    def notify(self, title: str, message: str) -> None:
        """A Windows notification from the tray icon; only logged when the icon isn't showing."""
        with self._lock:
            showing = self._ready and not self._stopping and not self._failed
        if not showing:
            log.info("%s: %s", title, message)
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            log.warning("Couldn't show the notification %r", title, exc_info=True)

    # --- pystray's threads ---------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._icon.run(setup=self._setup)
        except Exception:
            log.exception("The tray icon stopped working")
            with self._lock:
                self._failed = True
            if self._on_failed is not None:
                self._post(self._on_failed)

    def _setup(self, icon: Any) -> None:
        """pystray's setup thread, once its message loop runs."""
        with self._lock:
            self._ready = True
            stopping = self._stopping
            if not stopping:
                icon.visible = True
        if stopping:
            icon.stop()

    def _menu_action(self, callback: Callable[[], None]) -> Callable[..., None]:
        # pystray calls an action without arguments when its signature takes none.
        def action(*_args: object) -> None:
            self._post(callback)

        return action

    def _post(self, callback: Callable[[], None]) -> None:
        """Hand `callback` to the asyncio loop: the only thing the tray thread may do."""
        try:
            self._loop.call_soon_threadsafe(_run_logged, callback)
        except RuntimeError:
            pass  # the loop has closed: MediaForge is shutting down


def _import_pystray() -> Any:
    import pystray  # pyright: ignore[reportMissingTypeStubs]

    return pystray


def _run_logged(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        log.exception("Tray action failed")
