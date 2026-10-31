"""Decide which TMDB result a file is, and fetch the official names for it."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from mediaforge.api.tmdb import TmdbError
from mediaforge.db.matches import MatchRepo, RememberedMatch
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.tmdb import TmdbResult, TmdbSeason

log = logging.getLogger("mediaforge.matcher")

MAX_CANDIDATES = 8
_NOT_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


class TmdbLike(Protocol):
    async def search_movie(self, query: str, year: int | None) -> list[TmdbResult]: ...
    async def search_tv(self, query: str, year: int | None) -> list[TmdbResult]: ...
    async def details(self, kind: MediaKind, tmdb_id: int) -> TmdbResult | None: ...
    async def season(self, tv_id: int, number: int) -> TmdbSeason | None: ...


class Resolution(BaseModel, frozen=True):
    media: ResolvedMedia
    candidates: tuple[TmdbResult, ...] = ()


def normalize_title(text: str) -> str:
    text = text.casefold().replace("&", " and ")
    text = _NOT_WORD.sub(" ", text).replace("_", " ")
    return _SPACES.sub(" ", text).strip()


def match_key(parsed: ParsedMedia) -> tuple[MediaKind, str, int | None]:
    return parsed.kind, normalize_title(parsed.title), parsed.year


def choose(
    results: Sequence[TmdbResult], title: str, year: int | None
) -> tuple[MatchState, TmdbResult | None]:
    if not results:
        return MatchState.NOT_FOUND, None
    wanted = normalize_title(title)
    if year:
        for r in results:
            names = {normalize_title(r.title), normalize_title(r.original_title or "")}
            if r.year == year and wanted in names:
                return MatchState.CONFIDENT, r
    if len(results) == 1:
        return MatchState.CONFIDENT, results[0]
    return MatchState.UNCERTAIN, results[0]


class Resolver:
    def __init__(self, tmdb: TmdbLike | None, matches: MatchRepo) -> None:
        self._tmdb = tmdb
        self._matches = matches

    def set_client(self, tmdb: TmdbLike | None) -> None:
        """Swap the TMDB client, e.g. after the API key or language changed."""
        self._tmdb = tmdb

    async def resolve(self, parsed: ParsedMedia) -> Resolution:
        tmdb = self._tmdb
        if tmdb is None:
            return Resolution(media=ResolvedMedia.offline(parsed))
        try:
            remembered = await self._matches.get(*match_key(parsed))
            if remembered is not None:
                if remembered.tmdb_id is None:
                    return Resolution(media=ResolvedMedia.offline(parsed, MatchState.SKIPPED))
                details = await tmdb.details(parsed.kind, remembered.tmdb_id)
                if details is not None:
                    media = await self._build(parsed, details, MatchState.REMEMBERED)
                    return Resolution(media=media)
            if parsed.kind is MediaKind.EPISODE:
                results = await tmdb.search_tv(parsed.title, parsed.year)
            else:
                results = await tmdb.search_movie(parsed.title, parsed.year)
            state, chosen = choose(results, parsed.title, parsed.year)
            if chosen is None:
                return Resolution(media=ResolvedMedia.offline(parsed, MatchState.NOT_FOUND))
            media = await self._build(parsed, chosen, state)
            return Resolution(media=media, candidates=tuple(results[:MAX_CANDIDATES]))
        except TmdbError as exc:
            log.warning("TMDB lookup for %s failed: %s", parsed.source_name, exc)
            return Resolution(media=ResolvedMedia.offline(parsed))

    async def apply_choice(
        self, parsed: ParsedMedia, chosen: TmdbResult | None, *, remember: bool = True
    ) -> ResolvedMedia:
        kind, norm, year = match_key(parsed)
        if remember:
            label = f"{chosen.title} ({chosen.year})" if chosen else "Don't use TMDB"
            await self._matches.remember(
                RememberedMatch(
                    kind=kind,
                    norm_title=norm,
                    year=year,
                    tmdb_id=chosen.id if chosen else None,
                    label=label,
                )
            )
        if chosen is None:
            return ResolvedMedia.offline(parsed, MatchState.SKIPPED)
        try:
            return await self._build(parsed, chosen, MatchState.REMEMBERED)
        except TmdbError:
            return self._from_result(parsed, chosen, MatchState.REMEMBERED, None)

    @staticmethod
    def _from_result(
        parsed: ParsedMedia, chosen: TmdbResult, state: MatchState, season: TmdbSeason | None
    ) -> ResolvedMedia:
        episode_title = parsed.episode_title
        if season is not None and parsed.episode is not None:
            for ep in season.episodes:
                if ep.episode_number == parsed.episode and ep.name:
                    episode_title = ep.name
                    break
        return ResolvedMedia(
            parsed=parsed,
            title=chosen.title,
            year=chosen.year or parsed.year,
            episode_title=episode_title,
            match_state=state,
            tmdb_id=chosen.id,
            poster_path=chosen.poster_path,
            backdrop_path=chosen.backdrop_path,
            season_poster_path=season.poster_path if season else None,
        )

    async def _build(
        self, parsed: ParsedMedia, chosen: TmdbResult, state: MatchState
    ) -> ResolvedMedia:
        season: TmdbSeason | None = None
        if parsed.kind is MediaKind.EPISODE and parsed.season is not None and self._tmdb:
            try:
                season = await self._tmdb.season(chosen.id, parsed.season)
            except TmdbError as exc:
                log.warning("Season lookup for %s failed: %s", chosen.title, exc)
        return self._from_result(parsed, chosen, state, season)
