"""A plan: exactly what a run will do, worked out before anything changes on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from mediaforge.models.media import ResolvedMedia
from mediaforge.models.preset import PresetOptions


class PlanInput(BaseModel, frozen=True):
    item_id: int
    source: Path
    root: Path
    dropped_folder: Path | None
    media: ResolvedMedia


class PlannedFile(BaseModel, frozen=True):
    item_id: int
    source: Path
    target: Path
    skip_reason: str | None = None


class PlannedPoster(BaseModel, frozen=True):
    kind: Literal["movie", "series", "season"]
    tmdb_path: str
    target: Path
    item_ids: tuple[int, ...] = ()  # created only if one of these files made it


class PlannedTrailer(BaseModel, frozen=True):
    query: str
    title: str
    year: int | None
    target_stem: Path
    item_ids: tuple[int, ...] = ()


class Plan(BaseModel, frozen=True):
    files: tuple[PlannedFile, ...]
    posters: tuple[PlannedPoster, ...] = ()
    trailers: tuple[PlannedTrailer, ...] = ()
    clean_roots: tuple[Path, ...] = ()
    prune_dirs: tuple[Path, ...] = ()
    options: PresetOptions = PresetOptions()
