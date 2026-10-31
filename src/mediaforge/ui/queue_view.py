"""The Inbox (internally still the Queue view): what will be renamed, to what, and Forge.

Files appear as show and movie bands (AirGrabber's family look): "Needs you" above "Done", one
band per show with its episodes as short lines under it, one band per movie; the Series | Movies
switch in the top band picks which kind is shown.
"""

from __future__ import annotations

import asyncio
import functools
import itertools
import logging
import re
import time
import tkinter as tk
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any, Concatenate

import customtkinter as ctk

from mediaforge.api.trailers import ToolInstallError
from mediaforge.core.auto import library_naming
from mediaforge.core.executor import ProgressEvent, RunResult
from mediaforge.core.formatter import render
from mediaforge.core.planner import build_plan, describe
from mediaforge.core.queue import ItemStatus, QueueEvent, QueueItem
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import MediaKind
from mediaforge.models.preset import Preset, PresetOptions
from mediaforge.ui.detail_panel import DetailPanel
from mediaforge.ui.images import ImageCache, Key
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


_FINISHED = (ItemStatus.DONE, ItemStatus.SKIPPED)
_NEEDS_OWNER = (ItemStatus.NEEDS_CHOICE, ItemStatus.HELD, ItemStatus.ERROR)
_WAITING = (ItemStatus.HELD, ItemStatus.ERROR)  # a watched download held back, with a reason
DONE_TODAY = "Done today"


def item_kind(item: QueueItem) -> str:
    """Which side of the Series | Movies switch a file is on."""
    return "series" if item.media.kind is MediaKind.EPISODE else "movies"


@dataclass
class InboxRows:
    rows: list[Row] = field(default_factory=list[Row])
    targets: dict[int, Path] = field(default_factory=dict[int, Path])
    bands: dict[int, list[int]] = field(default_factory=dict[int, list[int]])  # band → items
    needs: int = 0  # files not yet renamed, both kinds
    other_kind: int = 0  # files on the other side of the switch


_OTHER = {"series": "movies", "movies": "series"}
_NOUNS = {"series": "shows", "movies": "movies"}
_SIDES = {"series": "Series", "movies": "Movies"}
_EMPTY_TITLE = "Drop video files or folders here"
_EMPTY_HINT = "or add them with the buttons below. Nothing is renamed until you click Forge."


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _seasons(group: Sequence[QueueItem]) -> str:
    numbers = sorted({i.media.parsed.season for i in group if i.media.parsed.season is not None})
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"Season {numbers[0]}"
    if numbers[-1] - numbers[0] == len(numbers) - 1:
        return f"Seasons {numbers[0]}–{numbers[-1]}"
    return f"Seasons {', '.join(map(str, numbers))}"


def _shared_folder(folders: Sequence[tuple[str, ...]]) -> str:
    """Where a band's files go: their folder, or the part their folders share ("Show › …")."""
    if all(f == folders[0] for f in folders):
        return " › ".join(folders[0]) or "the same folder"
    shared: list[str] = []
    for parts in zip(*folders, strict=False):
        if any(part != parts[0] for part in parts):
            break
        shared.append(parts[0])
    return " › ".join([*shared, "…"]) if shared else "several folders"


def episode_code(item: QueueItem) -> str:
    """S04E03 (or S01E02-E03), from the file itself rather than the naming rule."""
    parsed = item.media.parsed
    if parsed.season is None or parsed.episode is None:
        return ""
    code = f"S{parsed.season:02d}E{parsed.episode:02d}"
    if parsed.episode_end is not None and parsed.episode_end != parsed.episode:
        code += f"-E{parsed.episode_end:02d}"
    return code


def _needs_you(item: QueueItem) -> bool:
    """Dropped files not renamed yet, and downloads that wait for the owner."""
    if item.status in _FINISHED:
        return False
    return item.watched is None or item.status in _NEEDS_OWNER


def _done_on(item: QueueItem, today: date | None) -> bool:
    if item.status not in _FINISHED:
        return False
    if today is None or item.finished_at is None:
        return True
    return date.fromtimestamp(item.finished_at) == today


def build_inbox_rows(
    items: Sequence[QueueItem],
    fmt: str,
    kind: str,
    *,
    naming: Callable[[QueueItem], tuple[str, Path]] | None = None,
    waiting_title: str = "Would rename",
    today: date | None = None,
) -> InboxRows:
    """The Inbox's rows for one kind ("series", "movies" or "all").

    `naming` gives a watched download its library folder and naming rule; dropped files are
    renamed where they are with `fmt`. Only today's finished files are shown when `today` is set.
    """
    out = InboxRows()
    rendered = {}
    roots: dict[int, Path] = {}
    for item in items:
        item_fmt, root = (fmt, item.root)
        if naming is not None and item.watched is not None:
            item_fmt, root = naming(item)
        rendered[item.id] = render(item_fmt, item.media, item.path.suffix)
        roots[item.id] = root
        out.targets[item.id] = root.joinpath(*rendered[item.id].parts)
    per_kind = Counter(item_kind(i) for i in items)
    out.needs = sum(1 for i in items if _needs_you(i))
    out.other_kind = 0 if kind == "all" else sum(n for k, n in per_kind.items() if k != kind)
    shown = [i for i in items if kind == "all" or item_kind(i) == kind]
    keys = itertools.count(1)
    sections = (
        ("Needs you", [i for i in shown if _needs_you(i)]),
        (waiting_title, [i for i in shown if i.status not in _FINISHED and not _needs_you(i)]),
        (DONE_TODAY, [i for i in shown if _done_on(i, today)]),
    )

    def folders(item: QueueItem) -> tuple[str, ...]:
        library = (roots[item.id].name,) if item.watched is not None and naming else ()
        return (*library, *rendered[item.id].parts[:-1])

    for section, members in sections:
        if not members:
            continue
        out.rows.append(
            Row(key=-next(keys), kind="section", title=section, subtitle=str(len(members)))
        )
        groups: dict[tuple[str, int | None, bool], list[QueueItem]] = {}
        for item in members:
            media = item.media
            episodes = media.kind is MediaKind.EPISODE
            groups.setdefault((media.title.casefold(), media.year, episodes), []).append(item)
        for (_, _, episodes), group in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)
        ):
            group.sort(
                key=lambda i: (i.media.parsed.season or 0, i.media.parsed.episode or 0, i.path.name)
            )
            first = group[0].media
            unsure = any(i.status is ItemStatus.NEEDS_CHOICE for i in group)
            waiting = [i for i in group if i.watched is not None and i.status in _WAITING]
            where = _shared_folder([folders(i) for i in group])
            if unsure:
                subtitle = f"TMDB isn't sure · {_plural(len(group), 'file')}"
            elif waiting:
                reason = waiting[0].message or "Waiting for you"
                subtitle = f"{reason} · {_plural(len(group), 'file')}"
            elif episodes:
                count = _plural(len(group), "episode")
                subtitle = " · ".join(x for x in (_seasons(group), count) if x) + f" → {where}"
            else:
                subtitle = (
                    f"{_plural(len(group), 'file')} " if len(group) > 1 else ""
                ) + f"→ {where}"
            band_key = -next(keys)
            out.bands[band_key] = [i.id for i in group]
            if section == DONE_TODAY:
                times = [i.finished_at for i in group if i.finished_at]
                note = time.strftime("%H:%M", time.localtime(max(times))) if times else "Done"
                actions: tuple[tuple[str, str], ...] = (("undo", "Undo"),)
            else:
                note = ""
                actions = (
                    (("choose", "Choose…"), ("rename", "Rename"))
                    if unsure
                    else (("rename", "Rename"),)
                )
            out.rows.append(
                Row(
                    key=band_key,
                    kind="band",
                    title=(first.title if episodes else first.ny) + ("?" if unsure else ""),
                    subtitle=subtitle,
                    badge=first.title,
                    art=first.poster_path,
                    backdrop=first.backdrop_path,
                    actions=actions,
                    note=note,
                )
            )
            for item in group:
                name = rendered[item.id].parts[-1]
                title = item.media.episode_title if episodes and item.media.episode_title else name
                out.rows.append(
                    Row(
                        key=item.id,
                        kind="item",
                        title=title,
                        code=episode_code(item) if episodes else "",
                        faint=item.parsed.source_name,
                        status=item.status.value,
                        badge=first.title,
                    )
                )
    return out


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
        self.on_counts: Callable[[int, int], None] = lambda needs, other: None  # top band
        self.on_kind: Callable[[str], None] = lambda kind: None  # this side was empty
        self._bands: dict[int, list[int]] = {}
        self.images = ImageCache(self._fetch_image)
        self._loading: set[Key] = set()  # pictures being fetched right now
        self.busy = False  # set by @_one_at_a_time while Preview, Forge or Undo is going
        self._targets_in_run: dict[int, Path] = {}
        self._close_after_run: Callable[[], None] | None = None
        self._pending_preset: str | None = None
        self._targets: dict[int, Path] = {}
        self._refresh_id: str | None = None
        self._seen: set[int] = set()  # the items the last refresh showed
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
        self.title_label = ctk.CTkLabel(top, text="Inbox", font=font(16, "bold"), text_color=p.text)
        self.title_label.pack(side="left")
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
        self.list = VirtualList(holder, p, row_height=34, thumbnails=False)
        self.list.on_select = self._on_select
        self.list.on_remove = self._remove
        self.list.on_action = self._on_band_action
        self.list.images = self._images_for
        self.empty_state = ctk.CTkFrame(holder, fg_color="transparent")
        ctk.CTkLabel(self.empty_state, text="⤓", font=font(40), text_color=p.muted).pack(
            pady=(70, 6)
        )
        self.empty_title = ctk.CTkLabel(
            self.empty_state, text=_EMPTY_TITLE, font=font(15, "bold"), text_color=p.text
        )
        self.empty_title.pack()
        self.empty_hint = ctk.CTkLabel(
            self.empty_state, text=_EMPTY_HINT, font=font(12), text_color=p.muted
        )
        self.empty_hint.pack(pady=(4, 14))
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
        if kind != self.kind:
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
        seen, self._seen = self._seen, {i.id for i in items}
        inbox = self._inbox(items)
        if not inbox.rows and inbox.other_kind and self._seen - seen:
            # New files, all for the other side: show them (a click on an empty side stays).
            self.kind = _OTHER[self.kind]
            self.on_kind(self.kind)
            inbox = self._inbox(items)
        self._targets, self._bands = inbox.targets, inbox.bands
        self.list.set_rows(inbox.rows)
        if inbox.rows:
            self.empty_state.pack_forget()
            self.list.pack(fill="both", expand=True)
        else:
            self.list.pack_forget()
            if items:  # everything is on the other side of the switch
                self.empty_title.configure(text=f"No {_NOUNS[self.kind]} in the Inbox")
                self.empty_hint.configure(
                    text=f"The files waiting are under {_SIDES[_OTHER[self.kind]]}."
                )
            else:
                self.empty_title.configure(text=_EMPTY_TITLE)
                self.empty_hint.configure(text=_EMPTY_HINT)
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
        self.on_counts(inbox.needs, inbox.other_kind)

    def _inbox(self, items: Sequence[QueueItem]) -> InboxRows:
        settings = self.services.app_settings
        return build_inbox_rows(
            items,
            self.fmt,
            self.kind,
            naming=lambda item: library_naming(item, settings, self.presets),
            waiting_title="Renaming now" if settings.automatic == "on" else "Would rename",
            today=date.today(),
        )

    # --- bands -----------------------------------------------------------------------------

    async def _fetch_image(self, path: str, size: str) -> bytes | None:
        tmdb = self.services.tmdb
        return await tmdb.image(path, size) if tmdb is not None else None

    def _images_for(self, row: Row, width: int, height: int) -> tuple[Any, Any]:
        """A band's poster and backdrop when loaded; otherwise start loading them (again, if the
        cache let them go; never again once TMDB had none)."""
        inset = round(6 * self.list._get_widget_scaling())  # pyright: ignore[reportPrivateUsage]
        poster_h = height - 2 * inset
        poster_w = round(poster_h * 2 / 3)
        fade = self.palette.surface  # the band's own colour
        poster = self.images.cached_poster(row.art, poster_w, poster_h) if row.art else None
        backdrop = (
            self.images.cached_backdrop(row.backdrop, width, height, fade) if row.backdrop else None
        )
        wanted: list[tuple[Key, Awaitable[Any]]] = []
        if row.art and poster is None:
            key: Key = ("poster", row.art, poster_w, poster_h, "")
            if self._should_load(key):
                wanted.append((key, self.images.poster(row.art, poster_w, poster_h)))
        if row.backdrop and backdrop is None:
            key = ("backdrop", row.backdrop, width, height, fade)
            if self._should_load(key):
                wanted.append((key, self.images.backdrop(row.backdrop, width, height, fade)))
        if wanted:
            self._loading.update(key for key, _ in wanted)
            self.spawn(self._load_images(wanted))
        return poster, backdrop

    def _should_load(self, key: Key) -> bool:
        return key not in self._loading and not self.images.known_missing(key)

    async def _load_images(self, wanted: Sequence[tuple[Key, Awaitable[Any]]]) -> None:
        try:
            results = await asyncio.gather(*(load for _, load in wanted))
        finally:
            self._loading.difference_update(key for key, _ in wanted)
        if any(r is not None for r in results) and self.winfo_exists():
            self.list.refresh_images()

    def _on_band_action(self, key: int, action: str) -> None:
        ids = self._bands.get(key, [])
        if action == "rename":
            self.spawn(self.forge(only=ids))
        elif action == "undo":
            self.spawn(self.undo_band(ids))
        elif action == "choose":
            for item_id in ids:
                item = self.services.queue.get(item_id)
                if item is not None and item.status is ItemStatus.NEEDS_CHOICE:
                    self._change_match(item)
                    return

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

    async def _settle_uncertain(self, only: Sequence[int] | None = None) -> bool:
        uncertain = [
            i for i in self.services.queue.uncertain() if only is None or i.id in set(only)
        ]
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

    def _pending(self, only: Sequence[int] | None) -> list[QueueItem]:
        pending = self.pending_items()
        return pending if only is None else [i for i in pending if i.id in set(only)]

    @_one_at_a_time
    async def forge(self, only: Sequence[int] | None = None) -> None:
        """Rename every pending file, or only those in `only` (a band's Rename)."""
        await self.settled()
        if not self._pending(only) or not await self._settle_uncertain(only):
            return
        options = await self._trailer_options(self.options)
        items = self._pending(only)
        self._cancel = asyncio.Event()
        self.cancel_button.configure(command=self._cancel.set)
        self._set_running(True)
        try:
            result = RunResult()
            watched = [i.id for i in items if i.watched is not None]
            if watched:  # into the library, with the Series or Movies preset
                result = await self.services.auto.rename(
                    watched, source=RunSource.APP, on_progress=self._on_watched_progress
                )
            dropped = [i for i in items if i.watched is None]
            if dropped:
                async with self.services.run_lock:  # never alongside an automatic run
                    result = _merge(result, await self._forge_dropped(dropped, options))
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

    async def _forge_dropped(self, items: Sequence[QueueItem], options: PresetOptions) -> RunResult:
        queue = self.services.queue
        inputs = queue.plan_inputs([i.id for i in items])
        plan = await asyncio.to_thread(build_plan, inputs, self.fmt, options)
        self._targets_in_run = {f.item_id: f.target for f in plan.files}
        result = await self.services.executor.run(
            plan,
            source=RunSource.APP,
            preset_name=self.preset.name if self.preset else None,
            on_progress=self._on_progress,
            cancel=self._cancel,
            kinds={i.media.kind for i in items},
        )
        for item in items:  # a Done band's Undo needs the run
            if item.status is ItemStatus.DONE and item.run_id is None:
                item.run_id = result.run_id
        return result

    def _on_watched_progress(self, event: ProgressEvent) -> None:
        self.progress.set(event.fraction)
        self._schedule_refresh(60)

    async def undo_band(self, ids: Sequence[int]) -> None:
        """A Done band's Undo: put those files back; they leave the Inbox."""
        queue, history = self.services.queue, self.services.history
        items = [i for i in (queue.get(n) for n in ids) if i is not None and i.run_id is not None]
        op_ids: list[int] = []
        async with self.services.run_lock:
            for item in items:
                ops = await history.operations(item.run_id or 0)
                op_ids += [
                    o.id
                    for o in ops
                    if o.kind is OpKind.RENAME and o.dst == str(item.path) and o.undoable
                ]
            if not op_ids:
                log.info("Nothing to undo for these files.")
                return
            result = await self.services.undo.undo_operations(op_ids)
        for item in items:
            queue.remove(item.id)
        log.info("Put back %d file(s)", result.undone, extra={"success": True})

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


def _merge(a: RunResult, b: RunResult) -> RunResult:
    """One summary line for a Forge that renamed downloads and dropped files."""
    return RunResult(
        run_id=b.run_id or a.run_id,
        renamed=a.renamed + b.renamed,
        skipped=a.skipped + b.skipped,
        failed=a.failed + b.failed,
        posters=a.posters + b.posters,
        trailers=a.trailers + b.trailers,
        cancelled=a.cancelled or b.cancelled,
        errors=[*a.errors, *b.errors],
    )
