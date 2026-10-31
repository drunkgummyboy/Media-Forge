"""Work out every file's final name, plus the posters, trailers and cleanup a run needs."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from mediaforge.core import fsops
from mediaforge.core.formatter import render, sanitize_segment
from mediaforge.models.media import MediaKind
from mediaforge.models.plan import Plan, PlanInput, PlannedFile, PlannedPoster, PlannedTrailer
from mediaforge.models.preset import PresetOptions

ALREADY_NAMED = "already correctly named"

type PosterKind = Literal["movie", "series", "season"]


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _free_target(
    wanted: Path, source: Path, taken: set[str], exists: Callable[[Path], bool]
) -> Path:
    """The wanted name, or "name (n).ext" if something else already has it."""
    n = 0
    while True:
        candidate = wanted if n == 0 else wanted.with_name(f"{wanted.stem} ({n}){wanted.suffix}")
        is_source = _key(candidate) == _key(source)  # case-only rename of the same file
        if _key(candidate) not in taken and (is_source or not exists(candidate)):
            return candidate
        n += 1


def _series_root(item: PlanInput, folders: tuple[str, ...], target: Path) -> Path:
    """The first destination folder holding the series title; like the legacy app, the
    first folder when none does, and the file's own folder in a flat layout."""
    if not folders:
        return target.parent
    title = sanitize_segment(item.media.title).casefold()  # folders are sanitized too
    for i, folder in enumerate(folders):
        if title in folder.casefold():
            return item.root.joinpath(*folders[: i + 1])
    return item.root / folders[0]


def build_plan(
    inputs: Sequence[PlanInput],
    fmt: str,
    options: PresetOptions,
    exists: Callable[[Path], bool] = fsops.exists,
) -> Plan:
    files: list[PlannedFile] = []
    posters: dict[str, PlannedPoster] = {}
    trailers: dict[str, PlannedTrailer] = {}
    taken: set[str] = set()

    def add_poster(kind: PosterKind, tmdb_path: str | None, target: Path, item_id: int) -> None:
        key = _key(target)
        if key in posters:
            known = posters[key]
            posters[key] = known.model_copy(update={"item_ids": (*known.item_ids, item_id)})
        elif tmdb_path and not exists(target):
            posters[key] = PlannedPoster(
                kind=kind, tmdb_path=tmdb_path, target=target, item_ids=(item_id,)
            )

    for item in inputs:
        media = item.media
        rendered = render(fmt, media, item.source.suffix)
        wanted = item.root.joinpath(*rendered.parts)
        if str(wanted) == str(item.source):
            files.append(
                PlannedFile(
                    item_id=item.item_id,
                    source=item.source,
                    target=item.source,
                    skip_reason=ALREADY_NAMED,
                )
            )
            taken.add(_key(item.source))
            target = item.source
        else:
            target = _free_target(wanted, item.source, taken, exists)
            taken.add(_key(target))
            files.append(PlannedFile(item_id=item.item_id, source=item.source, target=target))

        ny = sanitize_segment(media.ny)
        item_id = item.item_id
        if media.kind is MediaKind.MOVIE and options.movie_poster:
            add_poster("movie", media.poster_path, target.parent / f"{ny} - Poster.jpg", item_id)
        if media.kind is MediaKind.EPISODE and options.series_poster:
            root = _series_root(item, rendered.parts[:-1], target)
            add_poster("series", media.poster_path, root / f"{ny} - Series Poster.jpg", item_id)
        if media.kind is MediaKind.EPISODE and options.season_poster and media.season is not None:
            name = f"Season {media.season:02d} Poster.jpg"
            add_poster("season", media.season_poster_path, target.parent / name, item_id)
        if options.trailer:
            stem = target.parent / f"{ny} - Trailer"
            key = _key(stem)
            if key in trailers:
                known = trailers[key]
                trailers[key] = known.model_copy(update={"item_ids": (*known.item_ids, item_id)})
            elif not exists(stem.with_name(stem.name + ".mp4")):
                words = [media.title, str(media.year)] if media.year else [media.title]
                trailers[key] = PlannedTrailer(
                    query=f"ytsearch1:{' '.join(words)} official trailer",
                    title=media.title,
                    year=media.year,
                    target_stem=stem,
                    item_ids=(item_id,),
                )

    clean_roots: list[Path] = []
    if options.clean_junk:
        for item in inputs:
            if item.dropped_folder and item.dropped_folder not in clean_roots:
                clean_roots.append(item.dropped_folder)

    prune: list[Path] = []
    if options.prune_empty:
        candidates = {_key(p): p for i in inputs for p in (i.source.parent, i.dropped_folder) if p}
        prune = sorted(candidates.values(), key=lambda p: len(p.parts), reverse=True)

    return Plan(
        files=tuple(files),
        posters=tuple(posters.values()),
        trailers=tuple(trailers.values()),
        clean_roots=tuple(clean_roots),
        prune_dirs=tuple(prune),
        options=options,
    )


def describe(plan: Plan) -> list[str]:
    """Plain-language lines for the Preview log."""
    lines = [f"Clean junk in: {root}" for root in plan.clean_roots]
    for f in plan.files:
        if f.skip_reason:
            lines.append(f"Skip: {f.source.name} ({f.skip_reason})")
        else:
            lines.append(f"Rename: {f.source.name} → {f.target}")
    lines += [f"Poster: {p.target}" for p in plan.posters]
    lines += [f"Trailer: {t.target_stem}.mp4" for t in plan.trailers]
    if plan.prune_dirs:
        lines.append(f"Remove empty folders: checks {len(plan.prune_dirs)} folder(s)")
    return lines
