"""The queue: files waiting to be forged, each with its parsed name and TMDB lookup."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple, Protocol

from pydantic import BaseModel

from mediaforge.core import fsops
from mediaforge.core.matcher import Resolution, match_key
from mediaforge.core.parser import is_video, parse_filename
from mediaforge.models.media import MatchState, ParsedMedia, ResolvedMedia, TypeHint
from mediaforge.models.plan import PlanInput
from mediaforge.models.tmdb import TmdbResult


class ItemStatus(StrEnum):
    LOOKING_UP = "looking_up"
    READY = "ready"
    NEEDS_CHOICE = "needs_choice"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


_FINISHED = frozenset({ItemStatus.DONE, ItemStatus.SKIPPED})


class ResolverLike(Protocol):
    async def resolve(self, parsed: ParsedMedia) -> Resolution: ...

    async def apply_choice(
        self, parsed: ParsedMedia, chosen: TmdbResult | None, *, remember: bool = True
    ) -> ResolvedMedia: ...


@dataclass
class QueueItem:
    id: int
    path: Path
    dropped_folder: Path | None
    parsed: ParsedMedia
    resolution: Resolution | None = None
    status: ItemStatus = ItemStatus.LOOKING_UP
    message: str = ""

    @property
    def root(self) -> Path:
        return self.dropped_folder or self.path.parent

    @property
    def media(self) -> ResolvedMedia:
        return self.resolution.media if self.resolution else ResolvedMedia.offline(self.parsed)


class QueueEvent(BaseModel):
    kind: Literal["added", "updated", "removed", "cleared"]
    ids: tuple[int, ...] = ()


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


class ScanResult(NamedTuple):
    files: list[tuple[Path, Path | None]]  # (video, the dropped folder it came from)
    folders: list[Path]  # the dropped paths that are folders


def scan_paths(paths: Sequence[Path], recursive: bool) -> ScanResult:
    """Find the videos to queue. Blocking (it touches the disk, maybe a slow NAS): run it
    in a worker thread."""
    files: list[tuple[Path, Path | None]] = []
    folders: list[Path] = []
    for path in paths:
        if fsops.is_dir(path):
            folders.append(path)
            if recursive:
                found = [folder / name for folder, names in fsops.walk(path) for name in names]
            else:
                found = fsops.list_files(path)
            videos = sorted((f for f in found if is_video(f)), key=lambda f: str(f).casefold())
            files.extend((f, path) for f in videos)
        elif is_video(path) and fsops.is_file(path):
            files.append((path, None))
    return ScanResult(files, folders)


PARSE_CHUNK = 8  # names parsed between turns of the event loop (about 40 ms of work)


async def _parse_all(names: Sequence[str], hint: TypeHint) -> list[ParsedMedia]:
    """Parse on the event loop in small chunks.

    guessit is pure-Python CPU work. In a worker thread it would compete with the Tk thread
    for the GIL and make the window stutter for seconds; here the window gets a turn
    between chunks.
    """
    parsed: list[ParsedMedia] = []
    for index, name in enumerate(names, start=1):
        parsed.append(parse_filename(name, hint))
        if index % PARSE_CHUNK == 0:
            await asyncio.sleep(0)
    return parsed


class Queue:
    def __init__(
        self,
        resolver: ResolverLike,
        on_change: Callable[[QueueEvent], None],
        lookup_concurrency: int = 6,
    ) -> None:
        self._resolver = resolver
        self._on_change = on_change
        self._limit = asyncio.Semaphore(lookup_concurrency)
        self._ids = itertools.count(1)
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._folders: list[Path] = []
        self.items: list[QueueItem] = []
        self.hint = TypeHint.AUTO
        self._adding = asyncio.Lock()  # one add at a time, so the same file can't slip in twice
        self._generation = 0  # bumped by clear(): an add that started before it is dropped

    def get(self, item_id: int) -> QueueItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    async def add_paths(self, paths: Sequence[Path], recursive: bool) -> int:
        async with self._adding:
            return await self._add_paths(paths, recursive)

    async def _add_paths(self, paths: Sequence[Path], recursive: bool) -> int:
        generation = self._generation
        scan = await asyncio.to_thread(scan_paths, list(paths), recursive)
        if generation != self._generation:
            return 0
        for folder in scan.folders:
            if _key(folder) not in {_key(f) for f in self._folders}:
                self._folders.append(folder)
        found = scan.files
        known = {_key(i.path): i for i in self.items}
        fresh: dict[str, tuple[Path, Path | None]] = {}
        updated: list[int] = []
        for file, folder in found:
            key = _key(file)
            existing = known.get(key)
            if existing is None:
                if key not in fresh or (folder is not None and fresh[key][1] is None):
                    fresh[key] = (file, folder)
            elif folder is not None and existing.dropped_folder is None:
                existing.dropped_folder = folder  # the dropped folder becomes its root
                updated.append(existing.id)
        if updated:
            self._on_change(QueueEvent(kind="updated", ids=tuple(updated)))
        if not fresh:
            return 0
        entries = list(fresh.values())
        parsed = await _parse_all([f.name for f, _ in entries], self.hint)
        if generation != self._generation:
            return 0
        new_items = [
            QueueItem(next(self._ids), file, folder, p)
            for (file, folder), p in zip(entries, parsed, strict=True)
        ]
        self.items.extend(new_items)
        self._on_change(QueueEvent(kind="added", ids=tuple(i.id for i in new_items)))
        for item in new_items:
            self._start_lookup(item)
        return len(new_items)

    def _start_lookup(self, item: QueueItem) -> None:
        old = self._tasks.pop(item.id, None)
        if old:
            old.cancel()
        item.status = ItemStatus.LOOKING_UP
        self._tasks[item.id] = asyncio.create_task(self._lookup(item))

    async def _lookup(self, item: QueueItem) -> None:
        try:
            async with self._limit:
                resolution = await self._resolver.resolve(item.parsed)
        finally:
            self._tasks.pop(item.id, None)
        if self.get(item.id) is not item:
            return
        item.resolution = resolution
        uncertain = resolution.media.match_state is MatchState.UNCERTAIN
        item.status = ItemStatus.NEEDS_CHOICE if uncertain else ItemStatus.READY
        self._on_change(QueueEvent(kind="updated", ids=(item.id,)))

    def remove(self, item_id: int) -> None:
        item = self.get(item_id)
        if item is None:
            return
        self.items.remove(item)
        task = self._tasks.pop(item_id, None)
        if task:
            task.cancel()
        self._on_change(QueueEvent(kind="removed", ids=(item_id,)))

    def clear(self) -> None:
        self._generation += 1
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self.items.clear()
        self._folders.clear()
        self._on_change(QueueEvent(kind="cleared"))

    async def wait_idle(self) -> None:
        while self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def set_hint(self, hint: TypeHint) -> None:
        if hint is self.hint:
            return
        self.hint = hint
        todo = self.pending()  # renamed files keep their result
        parsed = await _parse_all([i.path.name for i in todo], hint)
        for item, p in zip(todo, parsed, strict=True):
            if self.get(item.id) is not item or item.status in _FINISHED:
                continue
            item.parsed = p
            item.resolution = None
            self._start_lookup(item)
        self._on_change(QueueEvent(kind="updated", ids=tuple(i.id for i in todo)))

    async def choose_match(self, item_id: int, chosen: TmdbResult | None) -> None:
        item = self.get(item_id)
        if item is None or item.status in _FINISHED:
            return
        key = match_key(item.parsed)
        media = await self._resolver.apply_choice(item.parsed, chosen)
        changed = [item]
        item.resolution = Resolution(
            media=media, candidates=item.resolution.candidates if item.resolution else ()
        )
        for other in self.items:
            if (
                other is not item
                and other.status not in _FINISHED
                and (match_key(other.parsed) == key)
            ):
                other_media = await self._resolver.apply_choice(
                    other.parsed, chosen, remember=False
                )
                other.resolution = Resolution(
                    media=other_media,
                    candidates=other.resolution.candidates if other.resolution else (),
                )
                changed.append(other)
        for c in changed:
            c.status = ItemStatus.READY
        self._on_change(QueueEvent(kind="updated", ids=tuple(c.id for c in changed)))

    def pending(self) -> list[QueueItem]:
        """Items not renamed yet."""
        return [i for i in self.items if i.status not in _FINISHED]

    def mark_finished(self, item_id: int, new_path: Path | None) -> None:
        """A run renamed this file (new_path) or found it already right (None)."""
        item = self.get(item_id)
        if item is None:
            return
        if new_path is not None:
            item.path = new_path
            item.status = ItemStatus.DONE
        else:
            item.status = ItemStatus.SKIPPED
        self._on_change(QueueEvent(kind="updated", ids=(item_id,)))

    def uncertain(self) -> list[QueueItem]:
        return [i for i in self.items if i.status is ItemStatus.NEEDS_CHOICE]

    def plan_inputs(self, ids: Sequence[int] | None = None) -> list[PlanInput]:
        wanted = None if ids is None else set(ids)
        return [
            PlanInput(
                item_id=i.id,
                source=i.path,
                root=i.root,
                dropped_folder=i.dropped_folder,
                media=i.media,
            )
            for i in self.items
            if wanted is None or i.id in wanted
        ]

    async def rescan(self, recursive: bool) -> None:
        folders = list(self._folders)
        loose = [i.path for i in self.items if i.dropped_folder is None]
        self.clear()
        await self.add_paths([*folders, *loose], recursive)
