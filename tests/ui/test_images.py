"""Posters and backdrops for the bands: fetched once, faded into the window, never an error."""

import io

from PIL import Image

from mediaforge.api.tmdb import TmdbError
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.images import ImageCache, render_backdrop, render_poster


def picture(color: tuple[int, int, int], size: tuple[int, int] = (300, 170)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def test_backdrop_fades_into_the_window_colour() -> None:
    image = render_backdrop(picture((200, 30, 30)), 240, 64, fade_to="#161616")
    assert image.size == (240, 64)
    right = image.getpixel((239, 32))
    left = image.getpixel((0, 32))
    assert all(abs(c - 0x16) <= 2 for c in right[:3])  # the band's right end is the window
    assert left[0] > 60 and left[0] > left[1] + 30  # the picture still shows on the left


def test_poster_is_cropped_to_its_frame() -> None:
    image = render_poster(picture((10, 120, 200), (500, 300)), 34, 50)
    assert image.size == (34, 50)


async def test_images_are_fetched_once_and_cached(tk_root: DnDCTk) -> None:
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, size: str) -> bytes | None:
        calls.append((path, size))
        return picture((40, 90, 160))

    cache = ImageCache(fetch)
    assert cache.cached_backdrop("/b.jpg", 240, 64, "#161616") is None
    first = await cache.backdrop("/b.jpg", 240, 64, "#161616")
    second = await cache.backdrop("/b.jpg", 240, 64, "#161616")
    assert first is not None and first is second
    assert cache.cached_backdrop("/b.jpg", 240, 64, "#161616") is first
    assert calls == [("/b.jpg", "w780")]
    assert await cache.poster("/p.jpg", 34, 50) is not None
    assert calls[-1] == ("/p.jpg", "w185")


async def test_missing_image_gives_none(tk_root: DnDCTk) -> None:
    # Review focus 2: no key, offline, or no picture at TMDB: the band draws without it.
    async def offline(path: str, size: str) -> bytes | None:
        raise TmdbError("offline")

    async def nothing(path: str, size: str) -> bytes | None:
        return None

    assert await ImageCache(offline).backdrop("/b.jpg", 240, 64, "#161616") is None
    assert await ImageCache(nothing).poster("/p.jpg", 34, 50) is None
    assert await ImageCache(nothing).poster(None, 34, 50) is None
