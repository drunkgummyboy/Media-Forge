"""Posters and backdrops for the Inbox's bands, from TMDB through its disk cache.

The pictures are made ready with Pillow (cropped to their frame; a backdrop darkened and faded into
the window colour from left to right, as in the chosen mockup) and kept as Tk images in a small
LRU, on the loop thread: Tk images must never be made or freed elsewhere (see ui/gc_guard.py).
A picture that can't be had (no key, offline, none at TMDB) is simply None: the band draws without.
"""

from __future__ import annotations

import io
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from PIL import Image, ImageEnhance, ImageTk

from mediaforge.api.tmdb import TmdbError

log = logging.getLogger("mediaforge.ui")

POSTER_SIZE = "w185"
BACKDROP_SIZE = "w780"
type Fetch = Callable[[str, str], Awaitable[bytes | None]]
type Key = tuple[str, str, int, int, str]


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to fill width × height and crop the middle (CSS's `object-fit: cover`)."""
    scale = max(width / image.width, height / image.height)
    size = (max(width, round(image.width * scale)), max(height, round(image.height * scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    left = (image.width - width) // 2
    top = (image.height - height) // 3  # a little above the middle: faces, titles
    return image.crop((left, top, left + width, top + height))


def render_poster(data: bytes, width: int, height: int) -> Image.Image:
    return _cover(Image.open(io.BytesIO(data)).convert("RGB"), width, height)


def render_backdrop(data: bytes, width: int, height: int, fade_to: str) -> Image.Image:
    """The picture darkened, then covered from left to right with the window colour: a fifth
    at the left edge, fully at four fifths of the way and beyond."""
    picture = _cover(Image.open(io.BytesIO(data)).convert("RGB"), width, height)
    picture = ImageEnhance.Brightness(picture).enhance(0.6)
    solid = Image.new("RGB", (width, height), fade_to)
    ramp = Image.new("L", (width, 1))
    stop = max(1, round(width * 0.8))
    ramp.putdata([round(255 * min(1.0, 0.2 + 0.8 * x / stop)) for x in range(width)])
    return Image.composite(solid, picture, ramp.resize((width, height)))


class ImageCache:
    def __init__(self, fetch: Fetch, limit: int = 200) -> None:
        self._fetch = fetch
        self._limit = limit
        self._photos: OrderedDict[Key, ImageTk.PhotoImage] = OrderedDict()
        self._missing: set[Key] = set()

    def cached_poster(self, path: str | None, width: int, height: int) -> ImageTk.PhotoImage | None:
        return self._get(("poster", path or "", width, height, ""))

    def cached_backdrop(
        self, path: str | None, width: int, height: int, fade_to: str
    ) -> ImageTk.PhotoImage | None:
        return self._get(("backdrop", path or "", width, height, fade_to))

    def known_missing(self, key: Key) -> bool:
        return key in self._missing

    async def poster(self, path: str | None, width: int, height: int) -> ImageTk.PhotoImage | None:
        key: Key = ("poster", path or "", width, height, "")
        return await self._load(key, POSTER_SIZE, lambda data: render_poster(data, width, height))

    async def backdrop(
        self, path: str | None, width: int, height: int, fade_to: str
    ) -> ImageTk.PhotoImage | None:
        key: Key = ("backdrop", path or "", width, height, fade_to)
        return await self._load(
            key, BACKDROP_SIZE, lambda data: render_backdrop(data, width, height, fade_to)
        )

    def _get(self, key: Key) -> ImageTk.PhotoImage | None:
        photo = self._photos.get(key)
        if photo is not None:
            self._photos.move_to_end(key)
        return photo

    async def _load(
        self, key: Key, size: str, render: Callable[[bytes], Image.Image]
    ) -> ImageTk.PhotoImage | None:
        cached = self._get(key)
        if cached is not None or not key[1] or key in self._missing:
            return cached
        try:
            data = await self._fetch(key[1], size)
            photo = ImageTk.PhotoImage(render(data)) if data else None
        except (TmdbError, OSError, ValueError) as exc:
            log.debug("No %s for %s: %s", key[0], key[1], exc)
            photo = None
        if photo is None:
            self._missing.add(key)
            return None
        self._photos[key] = photo
        while len(self._photos) > self._limit:
            self._photos.popitem(last=False)
        return photo
