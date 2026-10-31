"""Carry out a Plan. Every change is journaled before it happens and settled right after."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel

from mediaforge.core import fsops
from mediaforge.core.cleanup import download_leftovers, find_cleanup_targets, videos_left
from mediaforge.db.history import HistoryRepo
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import MediaKind
from mediaforge.models.plan import Plan, PlannedTrailer

log = logging.getLogger("mediaforge.executor")
T = TypeVar("T")
_SUCCESS = {"success": True}


class ProgressEvent(BaseModel):
    item_id: int | None
    status: Literal["working", "done", "error", "skipped"]
    message: str
    fraction: float


class RunResult(BaseModel):
    run_id: int | None = None
    renamed: int = 0
    skipped: int = 0
    failed: int = 0
    posters: int = 0
    trailers: int = 0
    cleaned: int = 0
    pruned: int = 0
    cancelled: bool = False
    errors: list[str] = []


class PosterFetcher(Protocol):
    async def image(self, path: str, size: str) -> bytes: ...


class TrailerDownloader(Protocol):
    async def download(self, trailer: PlannedTrailer) -> Path | None: ...


def _remove_dir_or_fail(folder: Path) -> None:
    if not fsops.remove_empty_dir(folder):
        raise OSError("folder isn't empty")


class Executor:
    def __init__(
        self,
        history: HistoryRepo,
        posters: PosterFetcher | None,
        trailers: TrailerDownloader | None,
    ) -> None:
        self._history = history
        self._posters = posters
        self._trailers = trailers

    async def _journaled(self, op_id: int, work: Callable[[], T], *, stamped: bool = False) -> T:
        """Run one disk change in a thread and settle its journal row.

        If the run is cancelled meanwhile (window closed), the change still finishes, since
        it can't be stopped halfway, and is journaled before the cancellation goes through.
        """
        future = asyncio.ensure_future(asyncio.to_thread(work))
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        error = future.exception()
        if error is not None:
            await self._history.mark_failed(op_id, str(error))
        else:
            size, mtime = cast(tuple[int, int], future.result()) if stamped else (None, None)
            await self._history.mark_done(op_id, size=size, mtime_ns=mtime)
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error
        return future.result()

    async def _ensure_dir(self, run_id: int, folder: Path) -> None:
        for missing in await asyncio.to_thread(fsops.missing_dirs, folder):
            op = await self._history.record_pending(run_id, OpKind.CREATE_DIR, None, missing)
            try:
                await self._journaled(op, lambda m=missing: fsops.make_dir(m))
            except FileExistsError:
                continue  # it was there already: journaled as failed, never removed by undo

    async def run(
        self,
        plan: Plan,
        *,
        source: RunSource,
        preset_name: str | None,
        on_progress: Callable[[ProgressEvent], None],
        cancel: asyncio.Event,
        kinds: Collection[MediaKind] = (),
    ) -> RunResult:
        history = self._history
        result = RunResult()
        run_id = await history.start_run(source, preset_name, kinds=kinds)
        try:
            await self._clean(run_id, plan, result)
            succeeded = await self._files(run_id, plan, result, on_progress, cancel)
            if not result.cancelled:
                await self._artwork(run_id, plan, result, succeeded)
                await self._prune(run_id, plan, result)
                await self._leftovers(run_id, plan, result, succeeded)
        finally:
            kept = await history.finish_run(run_id)
            result.run_id = run_id if kept else None
        return result

    async def _clean(self, run_id: int, plan: Plan, result: RunResult) -> None:
        for root in plan.clean_roots:
            for junk in await asyncio.to_thread(find_cleanup_targets, root):
                await self._delete(run_id, junk, result)

    async def _delete(self, run_id: int, junk: Path, result: RunResult) -> None:
        op = await self._history.record_pending(run_id, OpKind.DELETE_FILE, junk, None)
        try:
            await self._journaled(op, lambda: fsops.remove_file(junk))
        except OSError as exc:
            log.warning("Couldn't remove %s: %s", junk, exc)
            return
        result.cleaned += 1
        log.info("Removed junk: %s", junk)

    async def _leftovers(self, run_id: int, plan: Plan, result: RunResult, ok: set[int]) -> None:
        """Clear a download's folder once all its files moved out and no other video is left."""
        for planned in plan.leftovers:
            if not set(planned.item_ids) <= ok:
                continue
            if await asyncio.to_thread(videos_left, planned.folder):
                continue
            for junk in await asyncio.to_thread(download_leftovers, planned.folder):
                await self._delete(run_id, junk, result)
            folders = await asyncio.to_thread(fsops.walk, planned.folder)
            for folder, _ in sorted(folders, key=lambda f: len(f[0].parts), reverse=True):
                if not await asyncio.to_thread(fsops.is_empty_dir, folder):
                    continue  # subtitles or pictures stay, and so does their folder
                op = await self._history.record_pending(run_id, OpKind.REMOVE_DIR, folder, None)
                try:
                    await self._journaled(op, lambda f=folder: _remove_dir_or_fail(f))
                except OSError:
                    continue
                result.pruned += 1
                log.info("Removed the download's folder: %s", folder)

    async def _files(
        self,
        run_id: int,
        plan: Plan,
        result: RunResult,
        on_progress: Callable[[ProgressEvent], None],
        cancel: asyncio.Event,
    ) -> set[int]:
        """Rename every file; returns the item ids now in their final place."""
        succeeded: set[int] = set()
        total = max(len(plan.files), 1)
        for index, planned in enumerate(plan.files):
            if cancel.is_set():
                result.cancelled = True
                log.warning("Run cancelled; %d file(s) not processed", len(plan.files) - index)
                break
            fraction = (index + 1) / total
            name = planned.source.name
            if planned.skip_reason:
                result.skipped += 1
                succeeded.add(planned.item_id)
                log.info("Skipped %s (%s)", name, planned.skip_reason)
                on_progress(
                    ProgressEvent(
                        item_id=planned.item_id,
                        status="skipped",
                        message=planned.skip_reason,
                        fraction=fraction,
                    )
                )
                continue
            on_progress(
                ProgressEvent(
                    item_id=planned.item_id,
                    status="working",
                    message=f"Renaming {name}",
                    fraction=fraction,
                )
            )
            try:
                await self._ensure_dir(run_id, planned.target.parent)
                op = await self._history.record_pending(
                    run_id, OpKind.RENAME, planned.source, planned.target
                )
                await self._journaled(op, lambda p=planned: fsops.move_file(p.source, p.target))
            except OSError as exc:
                result.failed += 1
                result.errors.append(f"{name}: {exc}")
                log.error("Couldn't rename %s: %s", name, exc)
                on_progress(
                    ProgressEvent(
                        item_id=planned.item_id, status="error", message=str(exc), fraction=fraction
                    )
                )
                continue
            result.renamed += 1
            succeeded.add(planned.item_id)
            log.info("Renamed: %s → %s", name, planned.target.name, extra=_SUCCESS)
            on_progress(
                ProgressEvent(
                    item_id=planned.item_id,
                    status="done",
                    message=planned.target.name,
                    fraction=fraction,
                )
            )
        return succeeded

    async def _artwork(self, run_id: int, plan: Plan, result: RunResult, ok: set[int]) -> None:
        if self._posters is not None:
            for poster in plan.posters:
                if not ok.intersection(poster.item_ids):
                    continue
                if await asyncio.to_thread(fsops.exists, poster.target):
                    continue
                try:
                    data = await self._posters.image(poster.tmdb_path, "w780")
                    await self._ensure_dir(run_id, poster.target.parent)
                except Exception as exc:  # network or disk: the run carries on without it
                    log.warning("Couldn't get poster %s: %s", poster.target.name, exc)
                    continue
                op = await self._history.record_pending(
                    run_id, OpKind.CREATE_FILE, None, poster.target
                )
                try:
                    await self._journaled(
                        op, lambda t=poster.target, d=data: fsops.write_new_file(t, d), stamped=True
                    )
                except OSError as exc:
                    log.warning("Couldn't save poster %s: %s", poster.target.name, exc)
                    continue
                result.posters += 1
                log.info("Saved poster: %s", poster.target.name, extra=_SUCCESS)

        if self._trailers is not None:
            for trailer in plan.trailers:
                if not ok.intersection(trailer.item_ids):
                    continue
                expected = trailer.target_stem.with_name(trailer.target_stem.name + ".mp4")
                op = await self._history.record_pending(run_id, OpKind.CREATE_FILE, None, expected)
                try:
                    created = await self._trailers.download(trailer)
                except Exception as exc:
                    await self._history.mark_failed(op, str(exc))
                    log.warning("Trailer for %s failed: %s", trailer.title, exc)
                    continue
                if created is None:
                    await self._history.mark_failed(op, "nothing downloaded")
                    continue
                size, mtime = await asyncio.to_thread(fsops.stamp, created)
                await self._history.mark_done(op, size=size, mtime_ns=mtime, dst=created)
                result.trailers += 1
                log.info("Saved trailer: %s", created.name, extra=_SUCCESS)

    async def _prune(self, run_id: int, plan: Plan, result: RunResult) -> None:
        for folder in plan.prune_dirs:
            if not await asyncio.to_thread(fsops.is_empty_dir, folder):
                continue
            op = await self._history.record_pending(run_id, OpKind.REMOVE_DIR, folder, None)
            try:
                await self._journaled(op, lambda f=folder: _remove_dir_or_fail(f))
            except OSError:
                continue
            result.pruned += 1
            log.info("Removed empty folder: %s", folder)
