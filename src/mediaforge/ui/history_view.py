"""History: the last 50 runs. Undo a whole run, or just the files you tick."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from mediaforge.core.undo import UndoResult
from mediaforge.models.history import (
    HISTORY_LIMIT,
    Operation,
    OpKind,
    OpState,
    RunSource,
    RunSummary,
    UndoState,
)
from mediaforge.models.media import MediaKind
from mediaforge.ui.theme import Palette, font
from mediaforge.ui.widgets.dialogs import ask_choice, confirm
from mediaforge.ui.widgets.virtual_list import Row, VirtualList

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.ui")
_NOTHING_YET = "Nothing here yet. Runs show up after you Forge some files."

_HIDDEN_KINDS = (OpKind.CREATE_DIR, OpKind.REMOVE_DIR)  # undone along with the files


def when_label(dt: datetime, now: datetime) -> str:
    if dt.date() == now.date():
        return f"Today {dt:%H:%M}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"Yesterday {dt:%H:%M}"
    if dt.year == now.year:
        return f"{dt.day} {dt:%b %H:%M}"
    return f"{dt.day} {dt:%b %Y %H:%M}"


def day_label(dt: datetime, now: datetime) -> str:
    """A day heading: "Today", "Yesterday", "Thu 29 Oct", "Tue 4 Mar 2025"."""
    if dt.date() == now.date():
        return "Today"
    if dt.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    text = f"{dt:%a} {dt.day} {dt:%b}"
    return text if dt.year == now.year else f"{text} {dt.year}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _describe(op: Operation) -> tuple[str, str]:
    """The name to show and a short note under it."""
    src = Path(op.src).name if op.src else ""
    dst = Path(op.dst).name if op.dst else ""
    match op.kind:
        case OpKind.RENAME:
            return dst, f"was {src}"
        case OpKind.CREATE_FILE:
            return dst, "downloaded"
        case OpKind.DELETE_FILE:
            return src, "junk file, deleted"
        case OpKind.CREATE_DIR:
            return dst, "new folder"
        case OpKind.REMOVE_DIR:
            return src, "empty folder, removed"


def _not_undoable_reason(op: Operation) -> str | None:
    if op.kind is OpKind.DELETE_FILE:
        return "can't be restored"
    if op.state is not OpState.DONE:
        return "didn't happen"
    if op.undo_state is UndoState.DONE:
        return "already undone"
    return None


class RunCard(ctk.CTkFrame):
    def __init__(self, master: Any, view: HistoryView, run: RunSummary, undone_at: datetime | None):
        p = view.palette
        super().__init__(master, fg_color=p.surface, corner_radius=8, border_width=0)
        self.view = view
        self.run = run
        self.undone_at = undone_at
        self.operations: list[Operation] = []
        self.checks: dict[int, bool] = {}
        self.files: VirtualList | None = None
        self.expanded = False
        dim = run.status == "undone"
        text_colour = p.muted if dim else p.text

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=8)
        head.grid_columnconfigure(1, weight=1)
        # A band, as in the Inbox: an accent stripe, the time large (the day is the heading).
        ctk.CTkFrame(self, width=4, fg_color=p.muted if dim else p.accent, corner_radius=2).place(
            x=3, rely=0.2, relheight=0.6
        )
        ctk.CTkLabel(
            head,
            text=f"{run.started_at:%H:%M}",
            width=70,
            anchor="w",
            font=font(15, "bold"),
            text_color=p.muted if dim else p.text,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text=self.summary_text(), anchor="w", font=font(12), text_color=text_colour
        ).grid(row=0, column=1, sticky="ew")
        tag = self.tag_text()
        if tag:
            ctk.CTkLabel(
                head,
                text=tag,
                font=font(10),
                text_color=p.muted,
                fg_color=p.raised,
                corner_radius=10,
            ).grid(row=0, column=2, padx=8, ipadx=6)
        self.toggle_button = ctk.CTkButton(
            head,
            text="Show files ▾",
            width=100,
            height=26,
            fg_color="transparent",
            border_width=1,
            border_color=p.border,
            text_color=text_colour,
            hover_color=p.raised,
            font=font(11),
            command=lambda: view.spawn(self.toggle()),
        )
        self.toggle_button.grid(row=0, column=3, padx=(0, 8))
        self.undo_button = ctk.CTkButton(
            head,
            text="Undo run",
            width=86,
            height=26,
            fg_color=p.accent,
            text_color=p.accent_text,
            hover_color=p.accent_hover,
            font=font(11, "bold"),
            command=lambda: view.spawn(view.undo_run(run.id)),
            state="disabled" if run.undoable_count == 0 else "normal",
        )
        if run.undoable_count == 0:
            self.undo_button.configure(fg_color=p.raised, text_color=p.muted)
        self.undo_button.grid(row=0, column=4)
        self.body = ctk.CTkFrame(self, fg_color="transparent")

    def summary_text(self) -> str:
        run = self.run
        text = f"{_plural(run.file_count, 'file')} · {run.preset_name or 'no preset'}"
        if run.created_count:
            text += f" · {run.created_count} downloaded"
        return text

    def tag_text(self) -> str:
        if self.run.status == "undone":
            return f"Undone {self.undone_at:%d %b}" if self.undone_at else "Undone"
        if self.run.status == "partly-undone":
            return "Partly undone"
        return {
            RunSource.RIGHT_CLICK: "right-click",
            RunSource.IMPORT: "imported",
            RunSource.AUTOMATIC: "automatic",
        }.get(self.run.source, "")

    async def toggle(self) -> None:
        if self.expanded:
            self.body.pack_forget()
            self.expanded = False
            self.toggle_button.configure(text="Show files ▾")
        else:
            await self.expand()

    def _build_body(self) -> VirtualList:
        p = self.view.palette
        files = VirtualList(self.body, p, row_height=40, removable=False, thumbnails=False)
        files.pack(fill="x")
        files.on_toggle = self.tick
        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.pack(fill="x", pady=(6, 0))
        self.selected_button = ctk.CTkButton(
            footer,
            text="",
            height=26,
            fg_color="transparent",
            border_width=1,
            border_color=p.border,
            text_color=p.text,
            hover_color=p.raised,
            font=font(11),
            command=lambda: self.view.spawn(self.view.undo_selected(self.run.id)),
        )
        self.selected_button.pack(side="right")
        return files

    async def expand(self) -> None:
        self.operations = await self.view.services.history.operations(self.run.id)
        if self.files is None:
            self.files = self._build_body()
        rows: list[Row] = []
        self.checks = {}
        for op in self.operations:
            if op.kind in _HIDDEN_KINDS:
                continue
            reason = _not_undoable_reason(op)
            self.checks[op.id] = reason is None
            title, note = _describe(op)
            rows.append(
                Row(
                    key=op.id,
                    kind="item",
                    title=title,
                    subtitle=f"{note}  ·  {reason}" if reason else note,
                    checked=reason is None,
                    check_disabled=reason is not None,
                )
            )
        # Only the rows on screen are drawn, so a run with hundreds of files opens at once.
        self.files.set_rows(rows)
        self.files.fit_height(320)
        self._update_count()
        self.body.pack(fill="x", padx=(40, 12), pady=(0, 10))
        self.expanded = True
        self.toggle_button.configure(text="Hide files ▴")

    def tick(self, op_id: int, ticked: bool) -> None:
        if op_id not in self.checks:
            return
        self.checks[op_id] = ticked
        if self.files is not None:
            for row in self.files.rows:
                if row.key == op_id and not row.check_disabled:
                    row.checked = ticked
            self.files.set_rows(self.files.rows)
        self._update_count()

    def selected_ids(self) -> list[int]:
        return [op_id for op_id, ticked in self.checks.items() if ticked]

    def _update_count(self) -> None:
        n = len(self.selected_ids())
        self.selected_button.configure(
            text=f"Undo {n} selected", state="normal" if n else "disabled"
        )


class HistoryView(ctk.CTkFrame):
    def __init__(self, master: Any, services: Services, palette: Palette) -> None:
        super().__init__(master, fg_color=palette.main, corner_radius=0)
        self.services = services
        self.palette = palette
        self.cards: list[RunCard] = []
        self.kind = "series"  # the Series | Movies switch
        self._headings: list[tuple[str, ctk.CTkLabel]] = []
        self._card_days: list[str] = []
        self._generation = 0  # bumped by each reload; an older reload stops building
        self.busy = False  # an undo is going; further clicks are ignored
        self._tasks: set[asyncio.Task[Any]] = set()
        self.confirm: Callable[[str, str], Awaitable[bool]] = lambda title, message: confirm(
            self, palette, title, message, "Undo"
        )
        self.inform: Callable[[str, str], Awaitable[None]] = self._inform

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(head, text="History", font=font(16, "bold"), text_color=palette.text).pack(
            side="left"
        )
        ctk.CTkLabel(
            head,
            text=f"your last {HISTORY_LIMIT} runs · newest first",
            font=font(12),
            text_color=palette.muted,
        ).pack(side="left", padx=10)
        self.list = ctk.CTkScrollableFrame(self, fg_color=palette.main, corner_radius=0)
        self.list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.empty_label = ctk.CTkLabel(
            self.list,
            text=_NOTHING_YET,
            font=font(12),
            text_color=palette.muted,
        )

    async def _inform(self, title: str, message: str) -> None:
        await ask_choice(self, self.palette, title, message, ["OK"])

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def set_kind(self, kind: str) -> None:
        """The Series | Movies switch: only runs that renamed this kind (old runs on both sides)."""
        if kind != self.kind:
            self.kind = kind
            self.spawn(self.reload())

    def on_show(self) -> None:
        self.spawn(self.reload())

    async def reload(self) -> None:
        self._generation += 1
        generation = self._generation
        every = await self.services.history.list_runs()
        if generation != self._generation:
            return
        wanted = MediaKind.MOVIE if self.kind == "movies" else MediaKind.EPISODE
        runs = [r for r in every if not r.kinds or wanted in r.kinds]
        for card in self.cards:
            card.destroy()
        for _, heading in self._headings:
            heading.destroy()
        self.cards = []
        self._headings = []
        self._card_days = []
        if not runs:
            noun = "movies" if self.kind == "movies" else "shows"
            text = f"No renamed {noun} yet" if every else _NOTHING_YET
            self.empty_label.configure(text=text)
            self.empty_label.pack(pady=40)
            return
        self.empty_label.pack_forget()
        now = datetime.now()
        for run in runs:
            undone_at = None
            if run.status == "undone":
                ops = await self.services.history.operations(run.id)
                dates = [o.undone_at for o in ops if o.undone_at]
                undone_at = max(dates) if dates else None
            if generation != self._generation:
                return  # a newer reload took over
            day = day_label(run.started_at, now)
            if not self._headings or self._headings[-1][0] != day:
                heading = ctk.CTkLabel(
                    self.list,
                    text=day.upper(),
                    anchor="w",
                    font=font(11, "bold"),
                    text_color=self.palette.muted,
                )
                heading.pack(fill="x", padx=10, pady=(12, 2))
                self._headings.append((day, heading))
            card = RunCard(self.list, self, run, undone_at)
            card.pack(fill="x", padx=6, pady=4)
            self.cards.append(card)
            self._card_days.append(day)
            await asyncio.sleep(0)  # one card at a time, so the window keeps responding

    def day_labels(self) -> list[str]:
        return [day for day, _ in self._headings]

    def cards_by_day(self) -> dict[str, list[RunCard]]:
        out: dict[str, list[RunCard]] = {}
        for card, day in zip(self.cards, self._card_days, strict=True):
            out.setdefault(day, []).append(card)
        return out

    def _card(self, run_id: int) -> RunCard | None:
        return next((c for c in self.cards if c.run.id == run_id), None)

    async def _report(self, result: UndoResult) -> None:
        log.info(
            "Undo finished: %s put back", _plural(result.undone, "change"), extra={"success": True}
        )
        if result.skipped:
            lines = [f"• {Path(path).name} ({reason})" for path, reason in result.skipped]
            await self.inform(
                "Some items weren't undone",
                f"{_plural(result.undone, 'change')} reversed. These were left as they are:\n\n"
                + "\n".join(lines[:15])
                + ("\n…" if len(lines) > 15 else ""),
            )
        await self.reload()

    async def undo_run(self, run_id: int) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await self._undo_run(run_id)
        finally:
            self.busy = False

    async def undo_selected(self, run_id: int) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await self._undo_selected(run_id)
        finally:
            self.busy = False

    async def _undo_run(self, run_id: int) -> None:
        card = self._card(run_id)
        if card is None:
            return
        run = card.run
        message = (
            f"This puts back {_plural(run.file_count, 'renamed file')} and removes "
            f"{_plural(run.created_count, 'downloaded poster or trailer')} from this run."
        )
        if not await self.confirm("Undo this run?", message):
            return
        await self._report(await self.services.undo.undo_run(run_id))

    async def _undo_selected(self, run_id: int) -> None:
        card = self._card(run_id)
        if card is None:
            return
        if not card.expanded:
            await card.expand()
        chosen = card.selected_ids()
        if not chosen:
            return
        # Folders created by the run go too, but only if undoing leaves them empty.
        folders = [o.id for o in card.operations if o.kind is OpKind.CREATE_DIR and o.undoable]
        if not await self.confirm(
            "Undo the selected items?", f"This puts back {_plural(len(chosen), 'item')}."
        ):
            return
        await self._report(await self.services.undo.undo_operations([*chosen, *folders]))

    def destroy(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        super().destroy()
