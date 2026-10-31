"""The Queue view: what will be renamed, to what, and the Forge button."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import tkinter as tk
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any, Concatenate

import customtkinter as ctk

from mediaforge.api.trailers import ToolInstallError
from mediaforge.core.executor import ProgressEvent
from mediaforge.core.formatter import render
from mediaforge.core.planner import build_plan, describe
from mediaforge.core.queue import ItemStatus, QueueEvent, QueueItem
from mediaforge.models.history import RunSource
from mediaforge.models.media import MediaKind
from mediaforge.models.preset import Preset, PresetOptions
from mediaforge.ui.detail_panel import DetailPanel
from mediaforge.ui.match_picker import MatchPicker
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.chips import Chip
from mediaforge.ui.widgets.dialogs import ask_choice
from mediaforge.ui.widgets.virtual_list import Row, VirtualList

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")

_SE = re.compile(r"S\d{2,}E\d{2,}(?:-E\d{2,})?")
OPTION_LABELS: dict[str, str] = {
    "movie_poster": "Movie poster",
    "series_poster": "Series poster",
    "season_poster": "Season posters",
    "trailer": "Trailer",
    "clean_junk": "Clean junk",
    "prune_empty": "Remove empty folders",
}
_PROGRESS_STATUS = {
    "working": ItemStatus.WORKING,
    "done": ItemStatus.DONE,
    "error": ItemStatus.ERROR,
    "skipped": ItemStatus.SKIPPED,
}

type AskFn = Callable[[str, str, Sequence[str]], Awaitable[str | None]]


def _one_at_a_time[**P](
    method: Callable[Concatenate[QueueView, P], Awaitable[None]],
) -> Callable[Concatenate[QueueView, P], Awaitable[None]]:
    """Ignore Preview/Forge/Undo while another one is still going (double clicks, keys)."""

    @functools.wraps(method)
    async def guarded(self: QueueView, *args: P.args, **kwargs: P.kwargs) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await method(self, *args, **kwargs)
        finally:
            self.busy = False

    return guarded


def build_rows(items: Sequence[QueueItem], fmt: str) -> tuple[list[Row], dict[int, Path]]:
    """Rows grouped by destination folder, plus each item's full target path."""
    groups: dict[str, list[Row]] = {}
    labels: dict[str, str] = {}
    counts: dict[str, tuple[int, bool]] = {}
    targets: dict[int, Path] = {}
    for item in items:
        media = item.media
        rendered = render(fmt, media, item.path.suffix)
        targets[item.id] = item.root.joinpath(*rendered.parts)
        if len(rendered.parts) > 1:
            group, label = "\\".join(rendered.parts[:-1]), rendered.parts[-2]
        elif media.kind is MediaKind.EPISODE:
            group = label = media.ny
        else:
            group, label = "", "Same folder"
        labels.setdefault(group, label)
        title = rendered.parts[-1]
        match = _SE.search(title)
        groups.setdefault(group, []).append(
            Row(
                key=item.id,
                kind="item",
                title=title,
                subtitle=item.path.name,
                badge=media.title,
                status=item.status.value,
                highlight=(match.start(), match.end()) if match else None,
            )
        )
        n, episodes = counts.get(group, (0, True))
        counts[group] = (n + 1, episodes and media.kind is MediaKind.EPISODE)
    rows: list[Row] = []
    for index, (group, members) in enumerate(groups.items(), start=1):
        n, episodes = counts[group]
        noun = "episode" if episodes else "file"
        icon = "📺" if episodes else "📁"
        rows.append(
            Row(
                key=-index,
                kind="group",
                title=f"{icon} {labels[group]}",
                subtitle=f"{n} {noun}{'s' if n != 1 else ''}",
            )
        )
        rows.extend(members)
    return rows, targets


class QueueView(ctk.CTkFrame):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self.presets: list[Preset] = []
        self.preset: Preset | None = None
        self.options = PresetOptions()
        self.running = False
        self.kind = "series"
        self.busy = False  # set by @_one_at_a_time while Preview, Forge or Undo is going
        self._targets_in_run: dict[int, Path] = {}
        self._close_after_run: Callable[[], None] | None = None
        self._pending_preset: str | None = None
        self._targets: dict[int, Path] = {}
        self._refresh_id: str | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._hint_task: asyncio.Task[None] | None = None
        self._cancel = asyncio.Event()
        self.ask: AskFn = lambda title, message, choices: ask_choice(
            self, palette, title, message, choices
        )

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_top()
        self._build_format()
        self._build_list()
        self._build_bottom()
        self.detail = DetailPanel(self, services, palette, self._change_match)
        self.detail.grid(row=0, column=1, rowspan=4, sticky="ns")
        tk.Frame(self, width=1, bg=palette.border).grid(row=0, column=1, rowspan=4, sticky="nsw")

        self._unsubscribe = services.queue_events.subscribe(self._on_queue_event)
        self.spawn(self._load_presets())
        self._bind_keys()
        self.refresh()

    # --- building --------------------------------------------------------------------------

    def _build_top(self) -> None:
        p = self.palette
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(top, text="Queue", font=font(16, "bold"), text_color=p.text).pack(side="left")
        self.count_label = ctk.CTkLabel(top, text="", font=font(12), text_color=p.muted)
        self.count_label.pack(side="left", padx=(10, 14))
        self.preset_menu = ctk.CTkOptionMenu(
            top,
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
        self.preset_menu.pack(side="left")
        for text, command in (
            ("Clear", self._clear),
            ("+ Files", self._browse_files),
            ("+ Folder", self._browse_folder),
        ):
            ctk.CTkButton(
                top,
                text=text,
                width=70,
                height=28,
                fg_color="transparent",
                border_width=1,
                border_color=p.border,
                text_color=p.text,
                hover_color=p.raised,
                font=font(12),
                command=command,
            ).pack(side="right", padx=(6, 0))

    def _build_format(self) -> None:
        p = self.palette
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=1, column=0, sticky="ew", padx=16)
        self.format_var = tk.StringVar(value="{ny}")
        self.format_entry = ctk.CTkEntry(
            box,
            textvariable=self.format_var,
            fg_color=p.surface,
            border_color=p.border,
            text_color=p.text,
            font=font(12, mono=True),
            height=30,
        )
        self.format_entry.pack(fill="x")
        self.format_var.trace_add("write", lambda *_: self._schedule_refresh(150))
        self.preview_line = ctk.CTkLabel(
            box,
            text="",
            font=font(11, mono=True),
            text_color=p.muted,
            anchor="w",
            justify="left",
        )
        self.preview_line.pack(fill="x", pady=(4, 6))

    def _build_list(self) -> None:
        p = self.palette
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.grid(row=2, column=0, sticky="nsew", padx=(8, 0))
        self.list = VirtualList(holder, p)
        self.list.on_select = self._on_select
        self.list.on_remove = self._remove
        self.empty_state = ctk.CTkFrame(holder, fg_color="transparent")
        ctk.CTkLabel(self.empty_state, text="⤓", font=font(40), text_color=p.muted).pack(
            pady=(70, 6)
        )
        ctk.CTkLabel(
            self.empty_state,
            text="Drop video files or folders here",
            font=font(15, "bold"),
            text_color=p.text,
        ).pack()
        ctk.CTkLabel(
            self.empty_state,
            text="or add them with the buttons below. Nothing is renamed until you click Forge.",
            font=font(12),
            text_color=p.muted,
        ).pack(pady=(4, 14))
        row = ctk.CTkFrame(self.empty_state, fg_color="transparent")
        row.pack()
        for text, command in (
            ("Add folder", self._browse_folder),
            ("Add files", self._browse_files),
        ):
            ctk.CTkButton(
                row,
                text=text,
                width=110,
                fg_color=p.raised,
                hover_color=p.border,
                text_color=p.text,
                font=font(12),
                command=command,
            ).pack(side="left", padx=6)

    def _build_bottom(self) -> None:
        p = self.palette
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=10)
        chips = ctk.CTkFrame(bar, fg_color="transparent")
        chips.pack(side="top", fill="x")
        actions = ctk.CTkFrame(bar, fg_color="transparent")
        actions.pack(side="top", fill="x", pady=(8, 0))
        self.chips: dict[str, Chip] = {}
        for key, label in OPTION_LABELS.items():
            chip = Chip(chips, label, p, lambda value, k=key: self._set_option(k, value))
            chip.pack(side="left", padx=(0, 6), pady=2)
            self.chips[key] = chip
        self.subfolder_chip = Chip(
            chips,
            "Scan subfolders",
            p,
            self._set_subfolders,
            value=self.services.app_settings.scan_subfolders,
        )
        self.subfolder_chip.pack(side="left", padx=(0, 6), pady=2)

        self.forge_button = ctk.CTkButton(
            actions,
            text="Forge",
            width=130,
            height=32,
            fg_color=p.accent,
            text_color=p.accent_text,
            hover_color=p.accent_hover,
            font=font(13, "bold"),
            command=lambda: self.spawn(self.forge()),
        )
        self.forge_button.pack(side="right")
        self.preview_button = ctk.CTkButton(
            actions,
            text="Preview",
            width=90,
            height=32,
            fg_color="transparent",
            border_width=1,
            border_color=p.border,
            text_color=p.text,
            hover_color=p.raised,
            font=font(12),
            command=lambda: self.spawn(self.preview()),
        )
        self.preview_button.pack(side="right", padx=(0, 8))
        self.cancel_button = ctk.CTkButton(
            actions,
            text="Cancel",
            width=80,
            height=32,
            fg_color="transparent",
            border_width=1,
            border_color=p.error,
            text_color=p.error,
            hover_color=p.raised,
            font=font(12),
            command=self._cancel.set,
        )
        self.progress = ctk.CTkProgressBar(actions, width=140, progress_color=p.accent)
        self.progress.set(0)

    def _bind_keys(self) -> None:
        top = self.winfo_toplevel()
        bindings = {
            "<Return>": "preview",
            "<Control-Return>": "forge",
            "<Control-z>": "undo",
            "<Control-Z>": "undo",
            "<Up>": "up",
            "<Down>": "down",
        }
        self._bindings: list[tuple[str, str]] = []
        for sequence, action in bindings.items():
            bind_id = top.bind(sequence, lambda e, a=action: self._key(a), add="+")
            self._bindings.append((sequence, bind_id))

    # --- helpers ---------------------------------------------------------------------------

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def set_kind(self, kind: str) -> None:
        """The Series | Movies switch (the Inbox shows one kind at a time)."""
        self.kind = kind
        self.refresh()

    def set_format(self, fmt: str) -> None:
        self.format_var.set(fmt)

    @property
    def fmt(self) -> str:
        return self.format_var.get()

    def pending_items(self) -> list[QueueItem]:
        return self.services.queue.pending()

    # --- presets & options -----------------------------------------------------------------

    async def _load_presets(self) -> None:
        self.presets = await self.services.presets.list()
        self.preset_menu.configure(values=[p.name for p in self.presets] or ["…"])
        wanted = self._pending_preset or self.services.app_settings.last_preset
        names = [p.name for p in self.presets]
        self.select_preset(wanted if wanted in names else names[0] if names else "")

    def reload_presets(self) -> None:
        self.spawn(self._load_presets())

    def select_preset(self, name: str) -> None:
        if not self.presets:
            self._pending_preset = name
            return
        preset = next((p for p in self.presets if p.name == name), None)
        if preset is None:
            return
        self._pending_preset = None
        self.preset = preset
        self.preset_menu.set(preset.name)
        self.options = preset.options.model_copy()
        for key, chip in self.chips.items():
            chip.set(getattr(self.options, key))
        self.set_format(preset.format)
        self._hint_task = self.spawn(self.services.queue.set_hint(preset.type))
        if self.services.app_settings.last_preset != preset.name:
            self.spawn(self._remember_preset(preset.name))

    async def _remember_preset(self, name: str) -> None:
        settings = await self.services.settings.load()
        self.services.app_settings = settings.model_copy(update={"last_preset": name})
        await self.services.settings.save(self.services.app_settings)

    def _set_option(self, key: str, value: bool) -> None:
        self.options = self.options.model_copy(update={key: value})

    def _set_subfolders(self, value: bool) -> None:
        async def apply() -> None:
            settings = await self.services.settings.load()
            self.services.app_settings = settings.model_copy(update={"scan_subfolders": value})
            await self.services.settings.save(self.services.app_settings)
            await self.services.queue.rescan(value)

        self.spawn(apply())

    # --- refreshing ------------------------------------------------------------------------

    def _on_queue_event(self, event: QueueEvent) -> None:
        self._schedule_refresh(80)

    def _schedule_refresh(self, delay_ms: int) -> None:
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        self._refresh_id = self.after(delay_ms, self.refresh)

    def refresh(self) -> None:
        self._refresh_id = None
        items = self.services.queue.items
        rows, self._targets = build_rows(items, self.fmt)
        self.list.set_rows(rows)
        if items:
            self.empty_state.pack_forget()
            self.list.pack(fill="both", expand=True)
        else:
            self.list.pack_forget()
            self.empty_state.pack(fill="both", expand=True)
        pending = len(self.pending_items())
        self.count_label.configure(text=f"{len(items)} file{'s' if len(items) != 1 else ''}")
        if not self.running:
            self.forge_button.configure(
                text=f"Forge {pending} file{'s' if pending != 1 else ''}" if pending else "Forge"
            )
        self._update_preview_line()
        selected = self.services.queue.get(self.list.selected) if self.list.selected else None
        self.detail.show(selected, self._targets.get(selected.id) if selected else None)

    def _update_preview_line(self) -> None:
        items = self.services.queue.items
        if not items:
            self.preview_line.configure(text="Add files to see how they'll be named.")
            return
        item = self.services.queue.get(self.list.selected or -1) or items[0]
        rendered = render(self.fmt, item.media, item.path.suffix)
        text = "↳ " + str(Path(item.root.name or str(item.root), *rendered.parts))
        if rendered.unknown:
            text += "   ⚠ not a known code: " + ", ".join("{" + u + "}" for u in rendered.unknown)
        self.preview_line.configure(text=text)

    def _on_select(self, key: int | None) -> None:
        item = self.services.queue.get(key) if key is not None else None
        self.detail.show(item, self._targets.get(key) if key is not None else None)
        self._update_preview_line()

    def move_selection(self, delta: int) -> None:
        keys = self.list.item_keys()
        if not keys:
            return
        if self.list.selected not in keys:
            self.list.select(keys[0] if delta > 0 else keys[-1])
            return
        index = keys.index(self.list.selected) + delta  # type: ignore[arg-type]
        self.list.select(keys[max(0, min(index, len(keys) - 1))])

    # --- actions ---------------------------------------------------------------------------

    def _key(self, action: str) -> str | None:
        return "break" if self.shortcut(action) else None

    def shortcut(self, action: str) -> bool:
        """Run a keyboard shortcut unless the user is typing in a text field."""
        if not self.winfo_ismapped():
            return False
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "Text", "TEntry"):
            return False
        if action == "preview":
            self.spawn(self.preview())
        elif action == "forge":
            self.spawn(self.forge())
        elif action == "undo":
            self.spawn(self.undo_latest())
        elif action in ("up", "down"):
            self.move_selection(-1 if action == "up" else 1)
        return True

    def _remove(self, key: int) -> None:
        self.services.queue.remove(key)

    def _clear(self) -> None:
        if not self.running:
            self.services.queue.clear()

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder with videos")
        if folder:
            self.spawn(self._add([Path(folder)]))

    def _browse_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Choose video files",
            filetypes=[
                ("Video files", "*.mkv *.mp4 *.avi *.mov *.m4v *.wmv *.flv"),
                ("All files", "*.*"),
            ],
        )
        if files:
            self.spawn(self._add([Path(f) for f in files]))

    async def _add(self, paths: Sequence[Path]) -> None:
        added = await self.services.queue.add_paths(
            list(paths), self.services.app_settings.scan_subfolders
        )
        if added:
            log.info("Added %d file(s) to the queue", added)

    def _change_match(self, item: QueueItem) -> None:
        self.spawn(self._pick_match(item))

    async def _pick_match(self, item: QueueItem) -> bool:
        choice = await MatchPicker.ask(self, item, self.services, self.palette)
        if choice == "cancel":
            return False
        await self.services.queue.choose_match(item.id, choice)
        return True

    async def _settle_uncertain(self) -> bool:
        uncertain = self.services.queue.uncertain()
        if not uncertain:
            return True
        n = len(uncertain)
        answer = await self.ask(
            "Some matches are uncertain",
            f"TMDB wasn't sure about {n} file{'s' if n != 1 else ''}. You can check them now "
            "or use TMDB's top result for each.",
            ["Review them", "Use top results", "Cancel"],
        )
        if answer == "Review them":
            for item in uncertain:
                if item.status is ItemStatus.NEEDS_CHOICE and not await self._pick_match(item):
                    return False
            return True
        if answer == "Use top results":
            for item in uncertain:
                item.status = ItemStatus.READY
            return True
        return False

    async def _trailer_options(self, options: PresetOptions) -> PresetOptions:
        trailers = self.services.trailers
        if not options.trailer:
            return options
        if not trailers.tools_present():
            answer = await self.ask(
                "Download trailer tools?",
                "Trailers need two free tools, yt-dlp and ffmpeg (about 110 MB, downloaded "
                "once from their official GitHub pages and checked before use).",
                ["Download", "Skip trailers"],
            )
            if answer != "Download":
                return options.model_copy(update={"trailer": False})
            self.progress.pack(side="right", padx=(0, 10))
            try:
                await trailers.install_tools(lambda step, fraction: self.progress.set(fraction))
            except ToolInstallError as exc:
                log.error("Couldn't install the trailer tools: %s", exc)
                return options.model_copy(update={"trailer": False})
            finally:
                self.progress.pack_forget()
        await trailers.maybe_update()
        return options

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        self.forge_button.configure(state=state)
        self.preview_button.configure(state=state)
        if running:
            self.progress.set(0)
            self.progress.pack(side="right", padx=(0, 10))
            self.cancel_button.pack(side="right", padx=(0, 8))
        else:
            self.progress.pack_forget()
            self.cancel_button.pack_forget()
            self.refresh()

    def _on_progress(self, event: ProgressEvent) -> None:
        self.progress.set(event.fraction)
        queue = self.services.queue
        if event.item_id is not None:
            if event.status == "done":
                queue.mark_finished(event.item_id, self._targets_in_run.get(event.item_id))
            elif event.status == "skipped":
                queue.mark_finished(event.item_id, None)
            elif (item := queue.get(event.item_id)) is not None:
                item.status = _PROGRESS_STATUS[event.status]
                item.message = event.message
        self._schedule_refresh(60)

    def request_close(self, close: Callable[[], None]) -> bool:
        """True if the window may close now; otherwise stop after the current file and
        call `close` once the run has finished and been recorded."""
        if not self.running:
            return True
        self._close_after_run = close
        self._cancel.set()
        log.warning("Stopping after the current file, then closing…")
        return False

    async def settled(self) -> None:
        if self._hint_task is not None:
            await self._hint_task
        await self.services.queue.wait_idle()

    @_one_at_a_time
    async def preview(self) -> None:
        await self.settled()
        items = self.pending_items()
        if not items:
            return
        inputs = self.services.queue.plan_inputs([i.id for i in items])
        plan = await asyncio.to_thread(build_plan, inputs, self.fmt, self.options)
        log.info("Preview (nothing has been changed):")
        for line in describe(plan):
            log.info(line)

    @_one_at_a_time
    async def forge(self) -> None:
        await self.settled()
        if not self.pending_items() or not await self._settle_uncertain():
            return
        options = await self._trailer_options(self.options)
        items = self.pending_items()
        self._cancel = asyncio.Event()
        self.cancel_button.configure(command=self._cancel.set)
        self._set_running(True)
        try:
            inputs = self.services.queue.plan_inputs([i.id for i in items])
            plan = await asyncio.to_thread(build_plan, inputs, self.fmt, options)
            self._targets_in_run = {f.item_id: f.target for f in plan.files}
            result = await self.services.executor.run(
                plan,
                source=RunSource.APP,
                preset_name=self.preset.name if self.preset else None,
                on_progress=self._on_progress,
                cancel=self._cancel,
            )
        finally:
            self._set_running(False)
            close, self._close_after_run = self._close_after_run, None
            if close is not None:
                self.after(10, close)
        summary = (
            f"Forged: {result.renamed} renamed, {result.skipped} already right, "
            f"{result.failed} failed"
        )
        extras = [f"{result.posters} poster(s)"] if result.posters else []
        extras += [f"{result.trailers} trailer(s)"] if result.trailers else []
        if extras:
            summary += " · " + ", ".join(extras)
        if result.cancelled:
            summary += " · cancelled"
        if result.failed:
            log.warning(summary)
        else:
            log.info(summary, extra={"success": True})

    @_one_at_a_time
    async def undo_latest(self) -> None:
        runs = [r for r in await self.services.history.list_runs() if r.status != "undone"]
        if not runs:
            log.info("Nothing to undo.")
            return
        run = runs[0]
        answer = await self.ask(
            "Undo the last run?",
            f"This puts back {run.file_count} renamed file(s) and removes {run.created_count} "
            "downloaded poster or trailer file(s) from that run.",
            ["Undo", "Cancel"],
        )
        if answer != "Undo":
            return
        result = await self.services.undo.undo_run(run.id)
        log.info("Undo finished: %d change(s) reversed", result.undone, extra={"success": True})

    def destroy(self) -> None:
        self._unsubscribe()
        top = self.winfo_toplevel()
        for sequence, bind_id in getattr(self, "_bindings", []):
            try:
                top.unbind(sequence, bind_id)
            except tk.TclError:
                pass
        for task in list(self._tasks):
            task.cancel()
        super().destroy()
