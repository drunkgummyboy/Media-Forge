from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from mediaforge.api.tmdb import TmdbError
from mediaforge.core.matcher import Resolver, choose, match_key, normalize_title
from mediaforge.db.database import Database
from mediaforge.db.matches import MatchRepo, RememberedMatch
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.tmdb import TmdbEpisode, TmdbResult, TmdbSeason


def res(id_: int, title: str, date: str | None = None, original: str | None = None) -> TmdbResult:
    return TmdbResult(
        id=id_,
        title=title,
        date=date,
        original_title=original,
        poster_path=f"/{id_}.jpg",
        backdrop_path=f"/b{id_}.jpg",
    )


class FakeTmdb:
    def __init__(self, results: list[TmdbResult] | None = None, fail: bool = False) -> None:
        self.results = results or []
        self.fail = fail
        self.searches: list[tuple[str, str, int | None]] = []

    async def search_movie(self, query: str, year: int | None) -> list[TmdbResult]:
        self.searches.append(("movie", query, year))
        if self.fail:
            raise TmdbError("offline")
        return self.results

    async def search_tv(self, query: str, year: int | None) -> list[TmdbResult]:
        self.searches.append(("tv", query, year))
        if self.fail:
            raise TmdbError("offline")
        return self.results

    async def details(self, kind: MediaKind, tmdb_id: int) -> TmdbResult | None:
        return res(tmdb_id, f"Details {tmdb_id}", "2005-03-24")

    async def season(self, tv_id: int, number: int) -> TmdbSeason | None:
        return TmdbSeason(
            season_number=number,
            poster_path=f"/s{number}.jpg",
            episodes=[TmdbEpisode(episode_number=2, name="Theseus")],
        )


@pytest_asyncio.fixture
async def matches(tmp_path: Path) -> AsyncIterator[MatchRepo]:
    db = await Database.open(tmp_path / "m.db")
    try:
        yield MatchRepo(db)
    finally:
        await db.close()


def episode(title: str = "VisionQuest", year: int | None = None) -> ParsedMedia:
    return ParsedMedia(
        source_name="x.mkv",
        kind=MediaKind.EPISODE,
        title=title,
        year=year,
        season=1,
        episode=2,
        episode_title="Parsed Name",
    )


def movie(title: str = "Dune", year: int | None = 2021) -> ParsedMedia:
    return ParsedMedia(source_name="x.mkv", kind=MediaKind.MOVIE, title=title, year=year)


def test_normalize() -> None:
    assert normalize_title("Fast & Furious: 9!") == "fast and furious 9"
    assert normalize_title("  The   Office ") == "the office"


def test_match_key() -> None:
    assert match_key(movie("Dune: Part One", 2021)) == (MediaKind.MOVIE, "dune part one", 2021)


def test_choose_confident_on_title_and_year() -> None:
    state, chosen = choose(
        [res(1, "Dune", "1984-12-14"), res(2, "Dune", "2021-10-22")], "Dune", 2021
    )
    assert (state, chosen and chosen.id) == (MatchState.CONFIDENT, 2)


def test_choose_confident_on_original_title() -> None:
    results = [res(1, "Other", "2019-01-01"), res(2, "Parasite", "2019-05-30", original="기생충")]
    state, chosen = choose(results, "기생충", 2019)
    assert (state, chosen and chosen.id) == (MatchState.CONFIDENT, 2)


def test_choose_single_result_confident() -> None:
    state, chosen = choose([res(5, "Something Else")], "Dune", None)
    assert (state, chosen and chosen.id) == (MatchState.CONFIDENT, 5)


def test_choose_uncertain_takes_top() -> None:
    state, chosen = choose(
        [res(1, "The Office", "2005-03-24"), res(2, "The Office", "2001-07-09")], "The Office", 2010
    )
    assert (state, chosen and chosen.id) == (MatchState.UNCERTAIN, 1)


def test_choose_no_year_multiple_is_uncertain() -> None:
    state, _ = choose(
        [res(1, "The Office", "2005-03-24"), res(2, "The Office", "2001-07-09")], "The Office", None
    )
    assert state is MatchState.UNCERTAIN


def test_choose_nothing() -> None:
    assert choose([], "x", None) == (MatchState.NOT_FOUND, None)


async def test_resolve_confident_movie(matches: MatchRepo) -> None:
    tmdb = FakeTmdb([res(2, "Dune", "2021-10-22")])
    resolution = await Resolver(tmdb, matches).resolve(movie())
    m = resolution.media
    assert (m.title, m.year, m.tmdb_id, m.poster_path, m.match_state) == (
        "Dune",
        2021,
        2,
        "/2.jpg",
        MatchState.CONFIDENT,
    )
    assert tmdb.searches == [("movie", "Dune", 2021)]


async def test_resolve_remembered_wins(matches: MatchRepo) -> None:
    await matches.remember(
        RememberedMatch(
            kind=MediaKind.EPISODE, norm_title="the office", year=None, tmdb_id=2316, label="US"
        )
    )
    tmdb = FakeTmdb([res(99, "Wrong Office")])
    resolution = await Resolver(tmdb, matches).resolve(episode("The Office"))
    assert resolution.media.match_state is MatchState.REMEMBERED
    assert resolution.media.tmdb_id == 2316
    assert tmdb.searches == []


async def test_resolve_remembered_skip_gives_skipped_offline_values(matches: MatchRepo) -> None:
    await matches.remember(
        RememberedMatch(
            kind=MediaKind.MOVIE, norm_title="dune", year=2021, tmdb_id=None, label="skip"
        )
    )
    resolution = await Resolver(FakeTmdb([res(2, "Dune")]), matches).resolve(movie())
    assert resolution.media.match_state is MatchState.SKIPPED
    assert resolution.media.title == "Dune" and resolution.media.tmdb_id is None


async def test_resolve_fetches_episode_title_and_season_poster(matches: MatchRepo) -> None:
    tmdb = FakeTmdb([res(7, "VisionQuest", "2026-01-01")])
    m = (await Resolver(tmdb, matches).resolve(episode())).media
    assert (m.title, m.year, m.episode_title, m.season_poster_path) == (
        "VisionQuest",
        2026,
        "Theseus",
        "/s1.jpg",
    )
    assert tmdb.searches == [("tv", "VisionQuest", None)]


async def test_resolve_offline_without_client(matches: MatchRepo) -> None:
    m = (await Resolver(None, matches).resolve(episode())).media
    assert m.match_state is MatchState.OFFLINE
    assert (m.title, m.episode_title) == ("VisionQuest", "Parsed Name")


async def test_set_client_switches_offline_to_online(matches: MatchRepo) -> None:
    resolver = Resolver(None, matches)
    assert (await resolver.resolve(movie())).media.match_state is MatchState.OFFLINE
    resolver.set_client(FakeTmdb([res(2, "Dune", "2021-10-22")]))
    assert (await resolver.resolve(movie())).media.match_state is MatchState.CONFIDENT


async def test_resolve_network_error_is_offline_not_crash(matches: MatchRepo) -> None:
    m = (await Resolver(FakeTmdb(fail=True), matches).resolve(movie())).media
    assert m.match_state is MatchState.OFFLINE


async def test_not_found(matches: MatchRepo) -> None:
    resolution = await Resolver(FakeTmdb([]), matches).resolve(movie())
    assert resolution.media.match_state is MatchState.NOT_FOUND
    assert resolution.candidates == ()


async def test_uncertain_keeps_candidates(matches: MatchRepo) -> None:
    results = [res(i, "The Office", f"{2000 + i}-01-01") for i in range(10)]
    resolution = await Resolver(FakeTmdb(results), matches).resolve(episode("The Office", 2020))
    assert resolution.media.match_state is MatchState.UNCERTAIN
    assert len(resolution.candidates) == 8


async def test_apply_choice_remembers(matches: MatchRepo) -> None:
    resolver = Resolver(FakeTmdb([]), matches)
    chosen = res(2316, "The Office", "2005-03-24")
    m = await resolver.apply_choice(episode("The Office"), chosen)
    assert (m.title, m.year, m.tmdb_id, m.match_state) == (
        "The Office",
        2005,
        2316,
        MatchState.REMEMBERED,
    )
    stored = await matches.get(MediaKind.EPISODE, "the office", None)
    assert stored is not None and stored.tmdb_id == 2316


async def test_apply_choice_skip_remembers_none(matches: MatchRepo) -> None:
    resolver = Resolver(FakeTmdb([]), matches)
    m = await resolver.apply_choice(movie(), None)
    assert m.match_state is MatchState.SKIPPED
    stored = await matches.get(MediaKind.MOVIE, "dune", 2021)
    assert stored is not None and stored.tmdb_id is None


async def test_backdrop_comes_with_the_match(matches: MatchRepo) -> None:
    r = Resolver(FakeTmdb([res(7, "Dune", "2021-09-15")]), matches)
    resolution = await r.resolve(movie("Dune", 2021))
    assert resolution.media.backdrop_path == "/b7.jpg"


async def test_remembered_match_keeps_its_backdrop(matches: MatchRepo) -> None:
    r = Resolver(FakeTmdb(), matches)
    chosen = res(42, "Heat", "1995-12-15")
    media = await r.apply_choice(movie("Heat", 1995), chosen)
    assert media.backdrop_path == "/b42.jpg"
    again = await r.resolve(movie("Heat", 1995))  # through TMDB's details for the remembered id
    assert again.media.backdrop_path == "/b42.jpg"


async def test_a_match_records_the_original_title_and_whether_tmdb_knows_the_episode(
    matches: MatchRepo,
) -> None:
    # Renaming on its own (1.3) checks both before moving a file by itself.
    tmdb = FakeTmdb([res(7, "Money Heist", "2017-05-02", original="La Casa de Papel")])
    found = (await Resolver(tmdb, matches).resolve(episode("La Casa de Papel"))).media
    assert found.original_title == "La Casa de Papel"
    assert found.episode_known  # the fake season has episode 2
    parsed = episode().model_copy(update={"episode": 9, "episode_title": "From The Name"})
    missing = (await Resolver(tmdb, matches).resolve(parsed)).media
    assert not missing.episode_known
    assert missing.episode_title == "From The Name"  # the name's own title is kept, as before
    assert not ResolvedMedia.offline(parsed).episode_known
