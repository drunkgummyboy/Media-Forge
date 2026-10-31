"""Async TMDB client: searches, season data, images. Never logs or leaks the API key."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import aiohttp
from pydantic import ValidationError

from mediaforge.db.cache import CacheRepo
from mediaforge.models.media import MediaKind
from mediaforge.models.tmdb import TmdbResult, TmdbSeason

log = logging.getLogger("mediaforge.tmdb")

JSON_TTL_S = 7 * 24 * 3600
IMAGE_TTL_S = 30 * 24 * 3600


class TmdbError(Exception):
    """TMDB couldn't be reached or answered with an error."""


class _NotFound(Exception):
    pass


class _Unauthorized(Exception):
    pass


class TmdbClient:
    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_URL = "https://image.tmdb.org/t/p"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        *,
        language: str = "en-US",
        cache: CacheRepo | None = None,
        images_dir: Path | None = None,
        max_concurrency: int = 8,
        retry_delays: Sequence[float] = (0.5, 1.0),
        base_url: str = BASE_URL,
        image_url: str = IMAGE_URL,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._image_url = image_url.rstrip("/")
        self._session = session
        self._key = api_key.strip()
        self.language = language
        self._cache = cache
        self._images_dir = images_dir
        self._limit = asyncio.Semaphore(max_concurrency)
        self._retry_delays = tuple(retry_delays)
        self._timeout = aiohttp.ClientTimeout(total=10)

    # --- plumbing -------------------------------------------------------------------------

    def redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """(query params, headers): v4 read-access tokens are JWTs and go in a header."""
        if self._key.startswith("eyJ"):
            return {}, {"Authorization": f"Bearer {self._key}"}
        return {"api_key": self._key}, {}

    async def _fetch(
        self, url: str, params: dict[str, str], headers: dict[str, str], *, binary: bool = False
    ) -> Any:
        attempts = len(self._retry_delays) + 1
        last_error = "no response"
        for attempt in range(attempts):
            retry_after: float | None = None
            try:
                async with (
                    self._limit,
                    self._session.get(
                        url, params=params, headers=headers, timeout=self._timeout
                    ) as resp,
                ):
                    if resp.status == 404:
                        raise _NotFound
                    if resp.status == 401:
                        raise _Unauthorized
                    if resp.status == 429 or resp.status >= 500:
                        last_error = f"HTTP {resp.status}"
                        header = resp.headers.get("Retry-After")
                        with contextlib.suppress(ValueError):
                            retry_after = float(header) if header else None
                    elif resp.status >= 400:
                        raise TmdbError(f"TMDB answered HTTP {resp.status}")
                    else:
                        return await (resp.read() if binary else resp.json())
            except (aiohttp.ClientError, TimeoutError) as exc:
                last_error = self.redact(f"{type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                delay = retry_after if retry_after is not None else self._retry_delays[attempt]
                await asyncio.sleep(delay)
        raise TmdbError(f"TMDB request failed ({last_error})")

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> Any | None:
        query: dict[str, str] = {k: str(v) for k, v in (params or {}).items()}
        query["language"] = self.language
        query["include_adult"] = "false"
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        if self._cache is not None:
            cached = await self._cache.get_json(cache_key, JSON_TTL_S)
            if cached is not None:
                return cached
        auth_params, headers = self._auth()
        try:
            data = await self._fetch(self._base_url + path, {**query, **auth_params}, headers)
        except _NotFound:
            return None
        except _Unauthorized as exc:
            raise TmdbError("TMDB rejected the API key") from exc
        if self._cache is not None:
            await self._cache.put_json(cache_key, data)
        return data

    @staticmethod
    def _results(data: Any) -> list[TmdbResult]:
        payload = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        raw = payload.get("results")
        items = cast(list[Any], raw) if isinstance(raw, list) else []
        results: list[TmdbResult] = []
        for item in items:
            try:
                results.append(TmdbResult.model_validate(item))
            except ValidationError:
                continue
        return results

    # --- API -----------------------------------------------------------------------------

    async def validate_key(self) -> bool:
        auth_params, headers = self._auth()
        try:
            await self._fetch(self._base_url + "/configuration", auth_params, headers)
        except _Unauthorized:
            return False
        return True

    async def search_movie(self, query: str, year: int | None) -> list[TmdbResult]:
        found: list[TmdbResult] = []
        if year:
            found += self._results(await self._get("/search/movie", {"query": query, "year": year}))
        found += self._results(await self._get("/search/movie", {"query": query}))
        seen: set[int] = set()
        unique: list[TmdbResult] = []
        for r in found:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)
        return unique

    async def search_tv(self, query: str, year: int | None) -> list[TmdbResult]:
        params: dict[str, str | int] = {"query": query}
        if year:
            params["first_air_date_year"] = year
        return self._results(await self._get("/search/tv", params))

    async def details(self, kind: MediaKind, tmdb_id: int) -> TmdbResult | None:
        path = f"/movie/{tmdb_id}" if kind is MediaKind.MOVIE else f"/tv/{tmdb_id}"
        data = await self._get(path)
        if data is None:
            return None
        try:
            return TmdbResult.model_validate(data)
        except ValidationError:
            return None

    async def season(self, tv_id: int, number: int) -> TmdbSeason | None:
        data = await self._get(f"/tv/{tv_id}/season/{number}")
        if data is None:
            return None
        try:
            return TmdbSeason.model_validate(data)
        except ValidationError:
            return None

    async def image(self, path: str, size: str) -> bytes:
        name = path.lstrip("/")
        cached = self._images_dir / size / name if self._images_dir else None
        if cached is not None and cached.exists():
            if time.time() - cached.stat().st_mtime < IMAGE_TTL_S:
                return await asyncio.to_thread(cached.read_bytes)
        try:
            data: bytes = await self._fetch(f"{self._image_url}/{size}/{name}", {}, {}, binary=True)
        except (_NotFound, _Unauthorized) as exc:
            raise TmdbError(f"Image {path} not available") from exc
        if cached is not None:
            await asyncio.to_thread(_write_atomic, cached, data)
        return data


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
