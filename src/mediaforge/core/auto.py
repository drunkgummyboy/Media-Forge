"""Renaming on its own: when a finished download is sure enough, where it goes, and the
`AutoRenamer` that looks at the watched download folders every few minutes.

A download is only renamed without a click when its file name and TMDB agree on what it is
(`decide`); everything else waits in the Inbox with one plain reason. Shows go to the Series
library folder with the Series preset, movies to the Movies folder with the Movies preset
(`library_runs`); a file whose name is already taken there waits instead of becoming a "(1)" copy.
Everything runs on the one asyncio loop; only listing folders happens in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from mediaforge.core import fsops
from mediaforge.core.executor import ProgressEvent, RunResult
from mediaforge.core.formatter import render
from mediaforge.core.library import inside, library_problems
from mediaforge.core.matcher import normalize_title
from mediaforge.core.planner import build_plan
from mediaforge.core.queue import ItemStatus, QueueItem
from mediaforge.core.watch import LOOK_INTERVAL_S, Watcher, candidates
from mediaforge.db.settings import AppSettings
from mediaforge.models.history import RunSource
from mediaforge.models.media import MatchState, MediaKind
from mediaforge.models.plan import Plan, PlanInput, PlannedLeftovers
from mediaforge.models.preset import DEFAULT_PRESETS, Preset

if TYPE_CHECKING:
    from mediaforge.app import Services

log = logging.getLogger("mediaforge.auto")

ALREADY_THERE = "Already in the library."
_NOT_SURE = {
    MatchState.UNCERTAIN: "TMDB isn't sure which one it is.",
    MatchState.NOT_FOUND: "Not found at TMDB.",
    MatchState.OFFLINE: "No TMDB key, or TMDB can't be reached.",
    MatchState.SKIPPED: "Skipped before.",
}
_DISAGREE = "The name and TMDB don't agree."


class Decision(NamedTuple):
    sure: bool
    reason: str  # empty when sure


def decide(item: QueueItem) -> Decision:
    """Whether `item` may be renamed and moved without the owner, and if not, why."""
    media, parsed = item.media, item.parsed
    if media.match_state is MatchState.REMEMBERED:
        return Decision(True, "")  # the owner confirmed this title before
    if media.match_state is not MatchState.CONFIDENT:
        return Decision(False, _NOT_SURE[media.match_state])
    if media.kind is MediaKind.MOVIE:
        if parsed.year is None:
            return Decision(False, "No year in the name: is it a movie?")
        if media.year != parsed.year:
            return Decision(False, _DISAGREE)
    else:
        if parsed.season is None or parsed.episode is None:
            return Decision(False, "No season or episode number in the name.")
        if not media.episode_known:
            return Decision(False, "TMDB doesn't know this episode.")
    names = {normalize_title(media.title), normalize_title(media.original_title or "")}
    if normalize_title(parsed.title) not in names:
        return Decision(False, _DISAGREE)
    return Decision(True, "")


class LibraryRun(NamedTuple):
    kind: MediaKind
    preset: Preset
    plan: Plan
    held: dict[int, str]  # item id → why it was left out of the plan


def _preset(name: str, fallback: str, presets: Sequence[Preset]) -> Preset:
    for source in (presets, DEFAULT_PRESETS):
        for preset in source:
            if preset.name == name:
                return preset
    return next(p for p in DEFAULT_PRESETS if p.name == fallback)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _leftovers(
    items: Sequence[QueueItem], plan: Plan, held: dict[int, str]
) -> tuple[PlannedLeftovers, ...]:
    """The download folders to clear after the move: below (never equal to) the watched folder,
    and only those every file of which is moving now."""
    moving = {f.item_id for f in plan.files}
    folders: dict[str, tuple[Path, list[int]]] = {}
    kept: set[str] = set()
    for item in items:
        folder = item.path.parent
        key = os.path.normcase(str(folder))
        if item.id not in moving or item.id in held:
            kept.add(key)
        elif item.watched is not None and inside(folder, item.watched):
            if not _same(folder, item.watched):
                folders.setdefault(key, (folder, []))[1].append(item.id)
    return tuple(
        PlannedLeftovers(folder=folder, item_ids=tuple(ids))
        for key, (folder, ids) in folders.items()
        if key not in kept
    )


def _runs(
    items: Sequence[QueueItem],
    settings: AppSettings,
    presets: Sequence[Preset],
    exists: Callable[[Path], bool],
    *,
    hold_taken: bool,
) -> list[LibraryRun]:
    runs: list[LibraryRun] = []
    for kind, folder, preset_name, fallback in (
        (MediaKind.EPISODE, settings.series_folder, settings.series_preset, "Series (Folder)"),
        (MediaKind.MOVIE, settings.movies_folder, settings.movies_preset, "Movie (Folder)"),
    ):
        mine = [i for i in items if i.media.kind is kind]
        if not mine:
            continue
        library = Path(folder)
        preset = _preset(preset_name, fallback, presets)
        options = preset.options.model_copy(update={"clean_junk": False, "prune_empty": False})
        for item in mine:
            if item.watched is None or not inside(item.path, item.watched):
                raise ValueError(f"{item.path} is outside the watched folders")
        inputs = [
            PlanInput(item_id=i.id, source=i.path, root=library, dropped_folder=None, media=i.media)
            for i in mine
        ]
        plan = build_plan(inputs, preset.format, options, exists)
        held: dict[int, str] = {}
        if hold_taken:
            wanted = {
                i.id: library.joinpath(*render(preset.format, i.media, i.path.suffix).parts)
                for i in mine
            }
            for planned in plan.files:
                if not _same(planned.target, wanted[planned.item_id]):
                    held[planned.item_id] = ALREADY_THERE
            plan = plan.model_copy(
                update={"files": tuple(f for f in plan.files if f.item_id not in held)}
            )
        if settings.clean_downloads:
            plan = plan.model_copy(update={"leftovers": _leftovers(mine, plan, held)})
        for planned in plan.files:
            if not inside(planned.target, library):
                raise ValueError(f"{planned.target} is outside the {kind.value} library folder")
        runs.append(LibraryRun(kind, preset, plan, held))
    return runs


def library_runs(
    items: Sequence[QueueItem],
    settings: AppSettings,
    presets: Sequence[Preset],
    exists: Callable[[Path], bool] = fsops.exists,
) -> list[LibraryRun]:
    """One run per kind for renaming on its own; a taken name holds the file instead."""
    return _runs(items, settings, presets, exists, hold_taken=True)


def owner_runs(
    items: Sequence[QueueItem],
    settings: AppSettings,
    presets: Sequence[Preset],
    exists: Callable[[Path], bool] = fsops.exists,
) -> list[LibraryRun]:
    """The same for the owner's Rename click: a taken name gets a numbered copy, as in 1.x."""
    return _runs(items, settings, presets, exists, hold_taken=False)


# --- the renamer ---------------------------------------------------------------------------------

SUMMARY_HOUR = 20  # the evening summary comes from 8 pm
UNDONE = "You undid this once; click Rename to move it again."
_NEEDS_OWNER = frozenset({ItemStatus.NEEDS_CHOICE, ItemStatus.HELD, ItemStatus.ERROR})
_DONE = frozenset({ItemStatus.DONE, ItemStatus.SKIPPED})

type WatchState = Literal["off", "trial", "watching", "unreachable", "setup"]


class WatchStatus(NamedTuple):
    state: WatchState
    text: str


class LookResult(NamedTuple):
    added: int
    renamed: int
    held: int
    unreachable: list[Path]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def needs_message(new: int) -> tuple[str, str]:
    verb = "needs" if new == 1 else "need"
    return f"{_plural(new, 'file')} {verb} you", "Open MediaForge to pick or confirm them."


def summary_message(episodes: int, movies: int) -> str:
    parts = [_plural(n, word) for n, word in ((episodes, "episode"), (movies, "movie")) if n]
    return f"Today: {' and '.join(parts)} renamed"


def summary_due(now: datetime, sent_on: str) -> bool:
    """From SUMMARY_HOUR, once a day (the caller also checks something was renamed)."""
    return now.hour >= SUMMARY_HOUR and sent_on != now.date().isoformat()


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _add(a: RunResult, b: RunResult) -> RunResult:
    return RunResult(
        run_id=b.run_id or a.run_id,
        renamed=a.renamed + b.renamed,
        skipped=a.skipped + b.skipped,
        failed=a.failed + b.failed,
        posters=a.posters + b.posters,
        trailers=a.trailers + b.trailers,
        cleaned=a.cleaned + b.cleaned,
        pruned=a.pruned + b.pruned,
        cancelled=a.cancelled or b.cancelled,
        errors=[*a.errors, *b.errors],
    )


class AutoRenamer:
    """Looks at the watched download folders and renames what it is sure about."""

    def __init__(
        self,
        services: Services,
        *,
        now: Callable[[], float] = time.time,
        interval_s: float = LOOK_INTERVAL_S,
    ) -> None:
        self._services = services
        self._now = now
        self._interval = interval_s
        self._watcher = Watcher(now=now)
        self._retry: set[int] = set()  # held for a passing reason (the NAS was away)
        self._undone: set[int] = set()  # put back by an undo: never moved on its own again
        self._lost: dict[str, Path] = {}  # folders that couldn't be reached at the last look
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._cancel = asyncio.Event()
        self.on_status: Callable[[WatchStatus], None] = lambda status: None
        self.on_needs: Callable[[int], None] = lambda count: None
        self.notify: Callable[[str, str], None] = lambda title, text: None

    # --- running on a timer ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._cancel = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        """Stop looking; a rename in progress stops after its current file."""
        self._stopping = True
        self._cancel.set()
        self._wake.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def look_soon(self) -> None:
        self._wake.set()

    def reconfigure(self) -> None:
        """The settings changed: start, stop, or look again at once."""
        if self._services.app_settings.automatic == "off":
            if self.running:
                asyncio.get_running_loop().create_task(self.stop())
            self.on_status(self.status([]))
        elif self.running:
            self.look_soon()
        else:
            self.start()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.look()
            except Exception:
                log.exception("Looking at the download folders failed")
            self._wake.clear()
            if self._stopping:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), self._interval)
            except TimeoutError:
                pass

    # --- one look ------------------------------------------------------------------------------

    def status(self, unreachable: Sequence[Path]) -> WatchStatus:
        settings = self._services.app_settings
        if settings.automatic == "off":
            return WatchStatus("off", "Not watching")
        if library_problems(settings):
            return WatchStatus("setup", "Set up watching in Settings")
        if unreachable:
            return WatchStatus("unreachable", f"Can't reach {unreachable[0]}")
        folders = _plural(len(settings.watch_folders), "folder")
        if settings.automatic == "trial":
            return WatchStatus("trial", f"Trial: watching {folders}")
        return WatchStatus("watching", f"Watching {folders}")

    async def look(self) -> LookResult:
        services, settings = self._services, self._services.app_settings
        if settings.automatic == "off" or library_problems(settings):
            self.on_status(self.status([]))
            return LookResult(0, 0, 0, [])
        queue = services.queue
        libraries = [Path(settings.series_folder), Path(settings.movies_folder)]
        lost_roots: list[Path] = []
        listed: list[tuple[Path, Path]] = []
        for root in map(Path, settings.watch_folders):
            try:
                files = await asyncio.to_thread(candidates, root)
            except OSError:
                lost_roots.append(root)
                continue
            listed.extend((f, root) for f in files)
        lost_libraries = [
            lib for lib in libraries if not await asyncio.to_thread(fsops.is_dir, lib)
        ]
        self._note_reachability([*lost_roots, *lost_libraries])

        finished = set(map(_key, self._watcher.finished([f for f, _ in listed])))
        known = {_key(i.path) for i in queue.items}
        fresh = [
            (file, root)
            for file, root in listed
            if _key(file) in finished
            and _key(file) not in known
            and not any(inside(file, lib) for lib in libraries)  # never taken back out
        ]
        undone = {_key(f) for f, _ in fresh if await services.history.was_undone(f)}
        new_ids = await queue.add_watched(fresh)
        await queue.wait_idle()

        held_before = self._needing_owner()
        for item_id in [*new_ids, *self._retry]:
            item = queue.get(item_id)
            if item is None or item.status in _DONE:
                continue
            if item_id in self._retry:
                item.status, item.message = ItemStatus.READY, ""
            if item_id in self._undone or _key(item.path) in undone:
                self._undone.add(item_id)
                queue.hold(item_id, UNDONE)
                continue
            verdict = decide(item)
            if not verdict.sure and item.status is not ItemStatus.NEEDS_CHOICE:
                queue.hold(item_id, verdict.reason)
        self._retry.clear()

        renamed = 0
        if settings.automatic == "on":
            movers = [
                i
                for i in queue.items
                if i.watched is not None
                and i.status is ItemStatus.READY
                and i.id not in self._undone
                and decide(i).sure
            ]
            if movers and lost_libraries:
                for item in movers:
                    queue.hold(item.id, f"Can't reach {lost_libraries[0]}.")
                    self._retry.add(item.id)
            elif movers:
                renamed = (await self._run(movers, RunSource.AUTOMATIC, owner=False)).renamed

        lost_keys = {_key(r) for r in lost_roots}
        present = {_key(f) for f, _ in listed} | {
            _key(i.path)
            for i in queue.items
            if i.watched is not None and _key(i.watched) in lost_keys
        }
        queue.forget_watched(present)
        midnight = datetime.fromtimestamp(self._now()).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        queue.purge_finished(midnight.timestamp())

        needing = self._needing_owner()
        new_needs = len(needing - held_before)
        if new_needs and settings.notify_needs:
            self.notify(*needs_message(new_needs))
        self.on_needs(len(needing))
        await self._maybe_summary()
        self.on_status(self.status([*lost_roots, *lost_libraries]))
        return LookResult(len(new_ids), renamed, len(needing), lost_roots)

    def _needing_owner(self) -> set[int]:
        return {
            i.id
            for i in self._services.queue.items
            if i.watched is not None and i.status in _NEEDS_OWNER
        }

    def _note_reachability(self, lost: Sequence[Path]) -> None:
        """Log a folder going away once, and coming back once: never at every look."""
        now_lost = {_key(p): p for p in lost}
        for key, path in now_lost.items():
            if key not in self._lost:
                log.warning("Can't reach %s", path)
        for key, path in self._lost.items():
            if key not in now_lost:
                log.info("Can reach %s again", path)
        self._lost = now_lost

    # --- renaming --------------------------------------------------------------------------

    async def rename(
        self,
        ids: Sequence[int],
        *,
        source: RunSource,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> RunResult:
        """The owner's Rename on watched downloads: into the library, whatever decide says."""
        queue = self._services.queue
        items = [
            i
            for i in (queue.get(item_id) for item_id in ids)
            if i is not None and i.watched is not None and i.status not in _DONE
        ]
        problems = library_problems(self._services.app_settings)
        if problems:
            for item in items:
                queue.hold(item.id, problems[0])
            return RunResult()
        for item in items:
            self._undone.discard(item.id)
            if item.status is ItemStatus.HELD:
                item.status, item.message = ItemStatus.READY, ""
        return await self._run(items, source, owner=True, on_progress=on_progress)

    async def _run(
        self,
        items: Sequence[QueueItem],
        source: RunSource,
        *,
        owner: bool,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> RunResult:
        services = self._services
        queue = services.queue
        total = RunResult()
        async with services.run_lock:
            presets = await services.presets.list()
            make = owner_runs if owner else library_runs
            runs = await asyncio.to_thread(make, items, services.app_settings, presets)
            for run in runs:
                for item_id, reason in run.held.items():
                    queue.hold(item_id, reason)
                if not run.plan.files:
                    continue
                plan = run.plan
                if plan.options.trailer and not services.trailers.tools_present():
                    options = plan.options.model_copy(update={"trailer": False})
                    plan = plan.model_copy(update={"options": options})
                targets = {f.item_id: f.target for f in plan.files}
                finished: list[int] = []

                def progress(
                    event: ProgressEvent,
                    targets: dict[int, Path] = targets,
                    finished: list[int] = finished,
                ) -> None:
                    if event.item_id is not None:
                        if event.status == "done":
                            queue.mark_finished(event.item_id, targets.get(event.item_id))
                            finished.append(event.item_id)
                        elif event.status == "error":
                            queue.hold(event.item_id, f"Couldn't move it: {event.message}")
                            if not owner:
                                self._retry.add(event.item_id)
                    if on_progress is not None:
                        on_progress(event)

                result = await services.executor.run(
                    plan,
                    source=source,
                    preset_name=run.preset.name,
                    on_progress=progress,
                    cancel=self._cancel,
                    kinds={run.kind},
                )
                for item_id in finished:
                    if (item := queue.get(item_id)) is not None:
                        item.run_id = result.run_id
                total = _add(total, result)
        return total

    async def _maybe_summary(self) -> None:
        services = self._services
        settings = services.app_settings
        now = datetime.fromtimestamp(self._now())
        if not settings.daily_summary or not summary_due(now, settings.summary_sent_on):
            return
        episodes = movies = 0
        for run in await services.history.list_runs():
            if run.source is not RunSource.AUTOMATIC or run.started_at.date() != now.date():
                continue
            if MediaKind.MOVIE in run.kinds:
                movies += run.file_count
            else:
                episodes += run.file_count
        if not episodes and not movies:
            return
        self.notify("MediaForge", summary_message(episodes, movies))
        today = {"summary_sent_on": now.date().isoformat()}
        services.app_settings = settings.model_copy(update=today)
        saved = await services.settings.load()
        await services.settings.save(saved.model_copy(update=today))
