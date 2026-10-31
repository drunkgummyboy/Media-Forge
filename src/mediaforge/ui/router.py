"""Routes launches handed over by other MediaForge processes: right-click batches open a
confirm window, plain launches bring the main window to the front."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaforge.platform.single_instance import ConfirmBatcher, InstanceMessage
from mediaforge.ui.confirm_window import ConfirmWindow
from mediaforge.ui.main_window import MainShell
from mediaforge.ui.theme import Palette

if TYPE_CHECKING:
    from mediaforge.app import Services

FLUSH_MS = 100


class WindowRouter:
    def __init__(
        self,
        root: Any,
        shell: MainShell,
        services: Services,
        palette: Palette,
        *,
        confirm_only: bool = False,
    ) -> None:
        self.root = root
        self.shell = shell
        self.services = services
        self.palette = palette
        self.confirm_only = confirm_only
        self.confirm_windows: list[ConfirmWindow] = []
        self.quit: Callable[[], None] = root.destroy
        self._batcher = ConfirmBatcher(on_batch=self.open_confirm)

    def start(self) -> None:
        """Check for finished right-click batches on a timer."""

        def tick() -> None:
            self.flush()
            if self.root.winfo_exists():
                self.root.after(FLUSH_MS, tick)

        self.root.after(FLUSH_MS, tick)

    def handle(self, msg: InstanceMessage) -> None:
        if msg.tray and not msg.paths:
            return  # a start in the tray (Windows startup) while this one already runs
        if msg.confirm:
            self._batcher.add(msg)
        else:
            self.show_main()
            self.shell.handle_instance_message(msg)

    def flush(self, now: float | None = None) -> None:
        self._batcher.flush_due(now)

    def show_main(self) -> None:
        self.confirm_only = False
        self.root.deiconify()
        self.root.lift()

    def open_confirm(self, preset: str | None, paths: Sequence[Path]) -> None:
        window = ConfirmWindow(self.root, self.services, self.palette, list(paths), preset)
        window.on_closed = self._closed
        window.on_open_in_main = self._open_in_main
        self.confirm_windows.append(window)

    def _open_in_main(self, paths: list[Path], preset: str | None) -> None:
        self.show_main()
        self.shell.handle_instance_message(
            InstanceMessage(paths=[str(p) for p in paths], preset=preset, confirm=False)
        )

    def _closed(self, window: ConfirmWindow) -> None:
        if window in self.confirm_windows:
            self.confirm_windows.remove(window)
        if self.confirm_only and not self.confirm_windows:
            self.quit()
