"""The right-click confirm window: every file's new name, nothing changes until Rename."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from mediaforge.core.planner import build_plan
from mediaforge.core.queue import ItemStatus, Queue, QueueEvent, QueueItem
from mediaforge.models.history import RunSource
from mediaforge.models.preset import Preset
from mediaforge.ui.match_picker import MatchPicker
from mediaforge.ui.queue_view import build_rows
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.popups import prepare_popup
from mediaforge.ui.widgets.virtual_list import VirtualList

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class ConfirmWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master: Any,
        services: Services,
        palette: Palette,
        paths: Sequence[Path],
        preset_name: str | None,
    ) -> None:
        super().__init__(master)
        self.services = services
        self.palette = palette
        self.paths = list(paths)
        self.presets: list[Preset] = []
        self.preset: Preset | None = None
        self.run_id: int | None = None
        self.busy = False  # renaming or undoing; extra clicks are ignored
        self.on_closed: Callable[[ConfirmWindow], None] = lambda win: None
        self.on_open_in_main: Callable[[list[Path], str | None], None] = lambda p, n: None
        self._ticks: dict[int, bool] = {}
        self._targets: dict[int, Path] = {}
        self._refresh_id: str | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.queue = Queue(services.resolver, self._on_event)
        p = palette

        self.title("MediaForge · Confirm rename")
        self.geometry("780x560")
        self.minsize(560, 360)
        self.configure(fg_color=p.main)
        prepare_popup(self)
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        self.heading = ctk.CTkLabel(
            head, text="Reading file names…", font=font(15, "bold"), text_color=p.text
        )
        self.heading.pack(side="left")
        self.preset_menu = ctk.CTkOptionMenu(
            head,
            values=["…"],
            width=170,
            fg_color=p.raised,
            button_color=p.raised,
            button_hover_color=p.border,
            text_color=p.text,
            dropdown_fg_color=p.surface,
            dropdown_text_color=p.text,
            dropdown_hover_color=p.raised,
            font=font(12),
            command=self.select_preset,
        )
        self.preset_menu.pack(side="left", padx=12)
        ctk.CTkLabel(
            head, text="Nothing changes until you click Rename", font=font(11), text_color=p.muted
        ).pack(side="right")

        # Only the rows on screen are drawn, so hundreds of files stay quick.
        self.list = VirtualList(self, p, removable=False)
        self.list.pack(fill="both", expand=True, padx=16, pady=6)
        self.list.on_toggle = self._on_toggle
        self.list.on_select = lambda key: self._update_counts()

        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.pack(fill="x", padx=16, pady=(4, 14))
        self.selected_label = ctk.CTkLabel(self.footer, text="", font=font(11), text_color=p.muted)
        self.selected_label.pack(side="left")
        self.result_label = ctk.CTkLabel(
            self.footer, text="", font=font(12, "bold"), text_color=p.success
        )
        self.rename_button = self._button("Rename", lambda: self._spawn(self.rename()), main=True)
        self.rename_button.pack(side="right")
        self.cancel_button = self._button("Cancel", self.cancel)
        self.cancel_button.pack(side="right", padx=8)
        self.open_button = self._button("Open in MediaForge", self.open_in_main, width=150)
        self.open_button.pack(side="right")
        self.change_button = self._button(
            "Change match…", lambda: self._spawn(self.change_selected()), width=130
        )
        self.change_button.pack(side="right", padx=8)
        self.undo_button = self._button("Undo", lambda: self._spawn(self.undo()))
        self.close_button = self._button("Close", self.close, main=True)

        self._load_task = self._spawn(self._load(preset_name))

    # --- plumbing --------------------------------------------------------------------------

    def _button(
        self, text: str, command: Callable[[], Any], main: bool = False, width: int = 110
    ) -> ctk.CTkButton:
        p = self.palette
        return ctk.CTkButton(
            self.footer,
            text=text,
            width=width,
            height=30,
            command=command,
            fg_color=p.accent if main else "transparent",
            border_width=0 if main else 1,
            border_color=p.border,
            text_color=p.accent_text if main else p.text,
            hover_color=p.warning if main else p.raised,
            font=font(12, "bold" if main else "normal"),
        )

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def loaded(self) -> None:
        await self._load_task
        await self.queue.wait_idle()
        self.refresh()

    async def _load(self, preset_name: str | None) -> None:
        self.presets = await self.services.presets.list()
        self.preset_menu.configure(values=[p.name for p in self.presets] or ["…"])
        names = [p.name for p in self.presets]
        wanted = preset_name if preset_name in names else self.services.app_settings.last_preset
        self.select_preset(wanted if wanted in names else names[0], reload=False)
        if self.preset is not None:
            await self.queue.set_hint(self.preset.type)
        await self.queue.add_paths(self.paths, self.services.app_settings.scan_subfolders)
        log.info("Confirm window opened for %s", _plural(len(self.queue.items), "file"))

    def select_preset(self, name: str, reload: bool = True) -> None:
        preset = next((p for p in self.presets if p.name == name), None)
        if preset is None:
            return
        self.preset = preset
        self.preset_menu.set(preset.name)
        if reload:
            self._spawn(self.queue.set_hint(preset.type))
        self._schedule_refresh()

    def _on_event(self, event: QueueEvent) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        self._refresh_id = self.after(80, self.refresh)

    # --- rows ------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_id = None
        if not self.winfo_exists() or self.preset is None:
            return
        items = self.queue.items
        rows, self._targets = build_rows(items, self.preset.format)
        by_id = {item.id: item for item in items}
        self._ticks = {item.id: self._ticks.get(item.id, True) for item in items}
        for row in rows:
            item = by_id.get(row.key)
            if item is None:
                continue
            row.checked = self._ticks[item.id]
            if item.status is ItemStatus.NEEDS_CHOICE:
                row.subtitle = f"TMDB isn't sure, using {item.media.ny}  ·  {row.subtitle}"
        self.list.set_rows(rows)
        self.heading.configure(text=f"Rename {_plural(len(items), 'file')}?")
        self._update_counts()

    def _on_toggle(self, item_id: int, ticked: bool) -> None:
        self._ticks[item_id] = ticked
        self._update_counts()

    def _update_counts(self) -> None:
        chosen = len(self.chosen_ids())
        self.selected_label.configure(text=f"{chosen} of {len(self._ticks)} selected")
        self.rename_button.configure(
            text=f"Rename {_plural(chosen, 'file')}", state="normal" if chosen else "disabled"
        )
        picked = self.list.selected is not None and not self.busy
        self.change_button.configure(state="normal" if picked else "disabled")

    def chosen_ids(self) -> list[int]:
        return [item_id for item_id, ticked in self._ticks.items() if ticked]

    def new_names(self) -> list[str]:
        return [self._targets[i.id].name for i in self.queue.items if i.id in self._targets]

    def set_ticked(self, path: Path, ticked: bool) -> None:
        for item in self.queue.items:
            if item.path == path and item.id in self._ticks:
                self._ticks[item.id] = ticked
        self.refresh()

    async def change_selected(self) -> None:
        item = next((i for i in self.queue.items if i.id == self.list.selected), None)
        if item is not None and not self.busy:
            await self._change(item)

    async def _change(self, item: QueueItem) -> None:
        choice = await MatchPicker.ask(self, item, self.services, self.palette)
        if choice != "cancel":
            await self.queue.choose_match(item.id, choice)

    # --- actions ---------------------------------------------------------------------------

    async def rename(self) -> None:
        if self.busy or self.preset is None:
            return
        self.busy = True
        try:
            await self._rename(self.preset)
        finally:
            self.busy = False

    def _set_buttons(self, state: str) -> None:
        for button in (
            self.rename_button,
            self.cancel_button,
            self.open_button,
            self.change_button,
        ):
            button.configure(state=state)

    async def _rename(self, preset: Preset) -> None:
        self._set_buttons("disabled")
        await self.queue.wait_idle()
        ids = self.chosen_ids()
        if not ids:
            self._set_buttons("normal")
            return
        options = preset.options
        if options.trailer and not self.services.trailers.tools_present():
            options = options.model_copy(update={"trailer": False})
            log.info("Trailers skipped: download the trailer tools from the main window first")
        inputs = self.queue.plan_inputs(ids)
        plan = await asyncio.to_thread(build_plan, inputs, preset.format, options)
        result = await self.services.executor.run(
            plan,
            source=RunSource.RIGHT_CLICK,
            preset_name=preset.name,
            on_progress=lambda event: None,
            cancel=asyncio.Event(),
        )
        self.run_id = result.run_id
        summary = f"{result.renamed} renamed"
        if result.skipped:
            summary += f", {result.skipped} already right"
        if result.failed:
            summary += f", {result.failed} failed"
        self.result_label.configure(
            text=summary, text_color=self.palette.error if result.failed else self.palette.success
        )
        log.info("Right-click rename: %s", summary, extra={"success": not result.failed})
        for widget in (
            self.selected_label,
            self.rename_button,
            self.cancel_button,
            self.open_button,
            self.change_button,
        ):
            widget.pack_forget()
        self.result_label.pack(side="left")
        self.close_button.pack(side="right")
        if self.run_id is not None:
            self.undo_button.pack(side="right", padx=8)

    async def undo(self) -> None:
        if self.busy or self.run_id is None:
            return
        run_id, self.run_id = self.run_id, None
        self.busy = True
        try:
            result = await self.services.undo.undo_run(run_id)
        finally:
            self.busy = False
        self.undo_button.pack_forget()
        text = f"Put back {_plural(result.undone, 'change')}"
        if result.skipped:
            text += f" ({len(result.skipped)} left as they are)"
        self.result_label.configure(text=text, text_color=self.palette.muted)

    def open_in_main(self) -> None:
        self.on_open_in_main(list(self.paths), self.preset.name if self.preset else None)
        self.close()

    def cancel(self) -> None:
        if self.busy:
            return  # renaming: closing now would cut the run short
        self.close()

    def close(self) -> None:
        self.queue.clear()
        self.on_closed(self)
        if self.winfo_exists():
            self.destroy()

    def destroy(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        super().destroy()
