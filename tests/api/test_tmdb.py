from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import pytest_asyncio

from mediaforge.api.tmdb import TmdbClient, TmdbError
from mediaforge.db.cache import CacheRepo
from mediaforge.db.database import Database
from mediaforge.models.media import MediaKind
from mediaforge.models.tmdb import TmdbResult

from helpers.fake_tmdb import Reply, running_fake

V3_KEY = "abcdef0123456789secret"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[Any]:
    async with running_fake() as f:
        yield f


@pytest_asyncio.fixture
async def cache(tmp_path: Path) -> AsyncIterator[CacheRepo]:
    db = await Database.open(tmp_path / "c.db")
    try:
        yield CacheRepo(db)
    finally:
        await db.close()


def movie(id_: int, title: str, date: str = "2021-10-22") -> dict[str, Any]:
    return {"id": id_, "title": title, "release_date": date, "poster_path": f"/{id_}.jpg"}


def client(session: aiohttp.ClientSession, fake: Any, key: str = V3_KEY, **kw: Any) -> TmdbClient:
    kw.setdefault("retry_delays", (0.0, 0.0))
    return TmdbClient(session, key, base_url=f"{fake.url}/3", image_url=f"{fake.url}/t/p", **kw)


async def test_v3_key_sent_as_query(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(json={"results": []}))
    await client(session, fake).search_movie("Dune", None)
    [seen] = fake.requests_to("/3/search/movie")
    assert seen.query["api_key"] == V3_KEY
    assert "Authorization" not in seen.headers


async def test_v4_token_sent_as_bearer(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(json={"results": []}))
    token = "eyJhbGciOiJIUzI1NiJ9.payload.sig"
    await client(session, fake, token).search_movie("Dune", None)
    [seen] = fake.requests_to("/3/search/movie")
    assert "api_key" not in seen.query
    assert seen.headers["Authorization"] == f"Bearer {token}"


async def test_movie_search_with_year_then_without_dedupes_in_order(
    session: aiohttp.ClientSession, fake: Any
) -> None:
    fake.add(
        "/3/search/movie",
        Reply(json={"results": [movie(1, "Dune")]}),
        Reply(json={"results": [movie(2, "Dune", "1984-12-14"), movie(1, "Dune")]}),
    )
    results = await client(session, fake).search_movie("Dune", 2021)
    assert [r.id for r in results] == [1, 2]
    first, second = fake.requests_to("/3/search/movie")
    assert first.query["year"] == "2021"
    assert "year" not in second.query


async def test_tv_search_uses_first_air_date_year(
    session: aiohttp.ClientSession, fake: Any
) -> None:
    fake.add(
        "/3/search/tv",
        Reply(json={"results": [{"id": 9, "name": "The Office", "first_air_date": "2005-03-24"}]}),
    )
    [result] = await client(session, fake).search_tv("The Office", 2005)
    assert fake.requests_to("/3/search/tv")[0].query["first_air_date_year"] == "2005"
    assert (result.title, result.year) == ("The Office", 2005)


async def test_include_adult_false_and_language_sent(
    session: aiohttp.ClientSession, fake: Any
) -> None:
    fake.add("/3/search/movie", Reply(json={"results": []}))
    await client(session, fake, language="nl-NL").search_movie("Dune", None)
    query = fake.requests_to("/3/search/movie")[0].query
    assert query["include_adult"] == "false"
    assert query["language"] == "nl-NL"


async def test_retry_on_429_then_success(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add(
        "/3/search/movie",
        Reply(status=429, json={}, headers={"Retry-After": "0"}),
        Reply(json={"results": [movie(1, "Dune")]}),
    )
    assert [r.id for r in await client(session, fake).search_movie("Dune", None)] == [1]


async def test_5xx_three_times_raises_tmdb_error(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(status=503, json={}))
    with pytest.raises(TmdbError):
        await client(session, fake).search_movie("Dune", None)
    assert len(fake.requests_to("/3/search/movie")) == 3


async def test_network_error_becomes_tmdb_error(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(disconnect=True))
    with pytest.raises(TmdbError):
        await client(session, fake).search_movie("Dune", None)


async def test_validate_key(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/configuration", Reply(status=401, json={}), Reply(json={"images": {}}))
    assert await client(session, fake).validate_key() is False
    assert await client(session, fake).validate_key() is True


async def test_season_404_returns_none(session: aiohttp.ClientSession, fake: Any) -> None:
    assert await client(session, fake).season(9, 7) is None


async def test_season_parsed(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add(
        "/3/tv/9/season/1",
        Reply(
            json={
                "season_number": 1,
                "poster_path": "/s1.jpg",
                "episodes": [{"episode_number": 2, "name": "Theseus", "extra": 1}],
            }
        ),
    )
    season = await client(session, fake).season(9, 1)
    assert season is not None
    assert season.poster_path == "/s1.jpg"
    assert season.episodes[0].name == "Theseus"


async def test_details(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/tv/9", Reply(json={"id": 9, "name": "The Office"}))
    result = await client(session, fake).details(MediaKind.EPISODE, 9)
    assert result is not None and result.title == "The Office"


async def test_cache_hit_skips_network(
    session: aiohttp.ClientSession, fake: Any, cache: CacheRepo
) -> None:
    fake.add("/3/search/movie", Reply(json={"results": [movie(1, "Dune")]}))
    c = client(session, fake, cache=cache)
    assert [r.id for r in await c.search_movie("Dune", None)] == [1]
    assert [r.id for r in await c.search_movie("Dune", None)] == [1]
    assert len(fake.requests_to("/3/search/movie")) == 1


async def test_cache_key_includes_language(
    session: aiohttp.ClientSession, fake: Any, cache: CacheRepo
) -> None:
    fake.add(
        "/3/search/movie",
        Reply(json={"results": [movie(1, "Dune")]}),
        Reply(json={"results": [movie(1, "Duin")]}),
    )
    await client(session, fake, cache=cache, language="en-US").search_movie("Dune", None)
    [nl] = await client(session, fake, cache=cache, language="nl-NL").search_movie("Dune", None)
    assert nl.title == "Duin"


async def test_cache_never_stores_the_key(
    session: aiohttp.ClientSession, fake: Any, cache: CacheRepo
) -> None:
    fake.add("/3/search/movie", Reply(json={"results": []}))
    await client(session, fake, cache=cache).search_movie("Dune", None)
    async with cache._db.conn.execute("SELECT key, payload FROM tmdb_cache") as cur:  # pyright: ignore[reportPrivateUsage]
        rows = await cur.fetchall()
    assert rows and all(V3_KEY not in key and V3_KEY not in payload for key, payload in rows)


async def test_image_cached_on_disk(
    session: aiohttp.ClientSession, fake: Any, tmp_path: Path
) -> None:
    fake.add("/t/p/w92/abc.jpg", Reply(body=b"jpegdata"))
    c = client(session, fake, images_dir=tmp_path / "img")
    assert await c.image("/abc.jpg", "w92") == b"jpegdata"
    assert await c.image("/abc.jpg", "w92") == b"jpegdata"
    assert len(fake.requests_to("/t/p/w92/abc.jpg")) == 1
    assert (tmp_path / "img" / "w92" / "abc.jpg").read_bytes() == b"jpegdata"


async def test_key_never_in_exception_text(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(status=500, json={}))
    with pytest.raises(TmdbError) as info:
        await client(session, fake).search_movie("Dune", None)
    assert V3_KEY not in str(info.value)
    assert client(session, fake).redact(f"x?api_key={V3_KEY}") == "x?api_key=***"


async def test_bad_results_are_skipped(session: aiohttp.ClientSession, fake: Any) -> None:
    fake.add("/3/search/movie", Reply(json={"results": [{"title": "no id"}, movie(3, "Ok")]}))
    assert [r.id for r in await client(session, fake).search_movie("x", None)] == [3]


def test_result_model_maps_tv_fields() -> None:
    r = TmdbResult.model_validate(
        {"id": 1, "name": "Shogun", "original_name": "SHOGUN", "first_air_date": "2024-02-27"}
    )
    assert (r.title, r.original_title, r.year) == ("Shogun", "SHOGUN", 2024)
    assert TmdbResult.model_validate({"id": 2, "title": "X", "release_date": ""}).year is None


def test_default_urls() -> None:
    assert TmdbClient.BASE_URL == "https://api.themoviedb.org/3"
    assert TmdbClient.IMAGE_URL == "https://image.tmdb.org/t/p"
