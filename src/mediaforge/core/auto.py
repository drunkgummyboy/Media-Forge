"""Renaming on its own: when a finished download is sure enough, and where it goes.

A download is only renamed without a click when its file name and TMDB agree on what it is
(`decide`); everything else waits in the Inbox with one plain reason. Shows go to the Series
library folder with the Series preset, movies to the Movies folder with the Movies preset
(`library_runs`); a file whose name is already taken there waits instead of becoming a "(1)" copy.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from mediaforge.core import fsops
from mediaforge.core.formatter import render
from mediaforge.core.library import inside
from mediaforge.core.matcher import normalize_title
from mediaforge.core.planner import build_plan
from mediaforge.core.queue import QueueItem
from mediaforge.db.settings import AppSettings
from mediaforge.models.media import MatchState, MediaKind
from mediaforge.models.plan import Plan, PlanInput, PlannedLeftovers
from mediaforge.models.preset import DEFAULT_PRESETS, Preset

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
