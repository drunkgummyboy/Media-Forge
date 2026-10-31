"""Read title, year, season and episode from release-style filenames (via guessit)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path, PurePath
from typing import Any

from guessit import guessit  # pyright: ignore[reportMissingTypeStubs]

from mediaforge.models.media import MediaKind, ParsedMedia, TypeHint

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv"}
)

# Properties guessit tends to get wrong for our purposes. Excluding them keeps those words
# in the title: "Dune Part Two", "Spider-Man" (not group "Spider"), "The Last of Us" (not "US").
_GUESSIT_OPTIONS: dict[str, Any] = {
    "excludes": [
        "part",
        "volume",
        "country",
        "release_group",
        "website",
        "streaming_service",
        "language",
        "subtitle_language",
    ]
}
_TAG_PROPERTIES = ("screen_size", "source", "video_codec", "audio_codec", "edition", "other")
_SPACES = re.compile(r"\s+")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def human_title(text: str) -> str:
    """Capitalise words that are entirely lowercase; leave NCIS and VisionQuest alone."""
    words = _SPACES.sub(" ", text).strip().split(" ")
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(str(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return str(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, list):
        value = value[0] if value else None  # pyright: ignore[reportUnknownVariableType]
    return value if isinstance(value, int) else None


def _tags(found: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    for prop in _TAG_PROPERTIES:
        value = found.get(prop)
        values: list[Any] = value if isinstance(value, list) else [value]  # pyright: ignore[reportUnknownVariableType]
        tags.extend(str(v) for v in values if v is not None)
    return tuple(tags)


def parse_filename(name: str, hint: TypeHint = TypeHint.AUTO) -> ParsedMedia:
    options = dict(_GUESSIT_OPTIONS)
    if hint is TypeHint.MOVIE:
        options["type"] = "movie"
    elif hint is TypeHint.TV:
        options["type"] = "episode"
    found: dict[str, Any] = dict(guessit(name, options))  # pyright: ignore[reportUnknownArgumentType]

    season = _as_int(found.get("season"))
    episodes = found.get("episode")
    episode_list: list[Any] = episodes if isinstance(episodes, list) else [episodes]  # pyright: ignore[reportUnknownVariableType]
    numbers = [e for e in episode_list if isinstance(e, int)]
    episode = numbers[0] if numbers else None
    episode_end = numbers[-1] if len(numbers) > 1 else None

    if hint is TypeHint.MOVIE:
        kind = MediaKind.MOVIE
    elif hint is TypeHint.TV:
        kind = MediaKind.EPISODE
    else:
        kind = MediaKind.EPISODE if (season is not None or episode is not None) else MediaKind.MOVIE

    if kind is MediaKind.EPISODE and season is None and episode is not None:
        season = 1

    year = _as_int(found.get("year"))
    date = found.get("date")
    if year is None and isinstance(date, dt.date):
        year = date.year

    title = human_title(_as_text(found.get("title")) or "")
    if not title:
        stem = PurePath(name).stem
        title = human_title(stem.replace(".", " ").replace("_", " ")) or "_"

    episode_title: str | None = None
    if kind is MediaKind.EPISODE:
        episode_title = human_title(_as_text(found.get("episode_title")) or "") or None

    return ParsedMedia(
        source_name=name,
        kind=kind,
        title=title,
        year=year,
        season=season,
        episode=episode,
        episode_end=episode_end,
        episode_title=episode_title,
        tags=_tags(found),
    )
