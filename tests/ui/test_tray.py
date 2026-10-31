"""The tray icon: MediaForge keeps watching with its window closed."""

import asyncio
import threading
from typing import Any

from PIL import Image

from mediaforge.ui.tray import TrayIcon, close_action


class FakeIcon:
    def __init__(self, name: str, image: Image.Image, title: str, menu: Any) -> None:
        self.icon, self.title, self.menu = image, title, menu
        self.visible = False
        self.notes: list[tuple[str, str]] = []
        self.stopped = threading.Event()

    def run(self, setup: Any) -> None:
        setup(self)
        self.stopped.wait(5)

    def stop(self) -> None:
        self.stopped.set()

    def notify(self, message: str, title: str) -> None:
        self.notes.append((title, message))


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items: Any) -> None:
        self.items = items


class FakeItem:
    def __init__(self, text: str, action: Any, default: bool = False) -> None:
        self.text, self.action, self.default = text, action, default


class FakePystray:
    Menu = FakeMenu
    MenuItem = FakeItem
    icons: list[FakeIcon] = []

    def Icon(self, name: str, image: Image.Image, title: str, menu: Any) -> FakeIcon:  # noqa: N802
        icon = FakeIcon(name, image, title, menu)
        self.icons.append(icon)
        return icon


async def started(calls: list[str]) -> tuple[TrayIcon, FakeIcon]:
    backend = FakePystray()
    backend.icons = []
    tray = TrayIcon(
        asyncio.get_running_loop(),
        Image.new("RGBA", (64, 64), (40, 90, 160, 255)),
        on_open=lambda: calls.append("open"),
        on_check_now=lambda: calls.append("check"),
        on_quit=lambda: calls.append("quit"),
        backend=backend,
    )
    tray.start()
    for _ in range(50):
        if backend.icons and backend.icons[0].visible:
            break
        await asyncio.sleep(0.02)
    return tray, backend.icons[0]


async def test_tray_menu_actions_reach_the_loop() -> None:
    calls: list[str] = []
    tray, icon = await started(calls)
    try:
        texts = [i.text for i in icon.menu.items if isinstance(i, FakeItem)]
        assert texts == ["Open MediaForge", "Check downloads now", "Quit"]
        assert next(i for i in icon.menu.items if isinstance(i, FakeItem)).default
        for item in icon.menu.items:
            if isinstance(item, FakeItem):
                # pystray calls these on its own thread; they must only post to the loop
                threading.Thread(target=item.action).start()
        for _ in range(50):
            if len(calls) == 3:
                break
            await asyncio.sleep(0.02)
        assert sorted(calls) == ["check", "open", "quit"]
    finally:
        tray.stop()


async def test_tray_count_tooltip() -> None:
    tray, icon = await started([])
    plain = icon.icon
    try:
        tray.set_count(2)
        assert icon.title == "MediaForge · 2 files need you"
        assert icon.icon is not plain  # a small count badge on the icon
        tray.set_count(1)
        assert icon.title == "MediaForge · 1 file needs you"
        tray.set_count(0)
        assert icon.title == "MediaForge" and icon.icon is plain
        tray.notify("2 files need you", "Open MediaForge to pick or confirm them.")
        assert icon.notes == [("2 files need you", "Open MediaForge to pick or confirm them.")]
    finally:
        tray.stop()


def test_close_hides_to_tray_when_watching() -> None:
    assert close_action(automatic="on", tray_running=True) == "hide"
    assert close_action(automatic="trial", tray_running=True) == "hide"


def test_close_quits_when_not_watching() -> None:
    assert close_action(automatic="off", tray_running=True) == "quit"
    assert close_action(automatic="on", tray_running=False) == "quit"  # no tray icon to go to
