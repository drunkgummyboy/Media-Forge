"""When a download may be renamed on its own, and where it goes."""

from pathlib import Path

import pytest

from mediaforge.core.auto import decide, library_runs, owner_runs
from mediaforge.core.matcher import Resolution
from mediaforge.core.queue import ItemStatus, QueueItem
from mediaforge.db.settings import AppSettings
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.preset import DEFAULT_PRESETS

DOWNLOADS = Path(r"C:\dl")
SERIES = Path(r"C:\lib\Series")
MOVIES = Path(r"C:\lib\Movies")
SETTINGS = AppSettings(
    watch_folders=[str(DOWNLOADS)], series_folder=str(SERIES), movies_folder=str(MOVIES)
)


def episode(
    title: str = "VisionQuest",
    season: int | None = 1,
    number: int | None = 2,
    *,
    state: MatchState = MatchState.CONFIDENT,
    tmdb_title: str | None = None,
    original: str | None = None,
    known: bool = True,
    item_id: int = 1,
) -> QueueItem:
    name = f"{title}.S{season or 0:02d}E{number or 0:02d}.mkv"
    parsed = ParsedMedia(
        source_name=name, kind=MediaKind.EPISODE, title=title, season=season, episode=number
    )
    media = ResolvedMedia(
        parsed=parsed,
        title=tmdb_title or title,
        original_title=original,
        year=2025,
        episode_title="Theseus" if known else None,
        episode_known=known,
        match_state=state,
        tmdb_id=1,
    )
    path = DOWNLOADS / name.removesuffix(".mkv") / name
    return QueueItem(
        item_id, path, None, parsed, Resolution(media=media), ItemStatus.READY, watched=DOWNLOADS
    )


def movie(
    title: str = "Dune",
    year: int | None = 2021,
    *,
    tmdb_year: int = 2021,
    state: MatchState = MatchState.CONFIDENT,
    item_id: int = 1,
) -> QueueItem:
    name = f"{title}.{year}.mkv" if year else f"{title}.mkv"
    parsed = ParsedMedia(source_name=name, kind=MediaKind.MOVIE, title=title, year=year)
    media = ResolvedMedia(
        parsed=parsed, title=title, year=tmdb_year, episode_title=None, match_state=state, tmdb_id=2
    )
    return QueueItem(
        item_id,
        DOWNLOADS / name,
        None,
        parsed,
        Resolution(media=media),
        ItemStatus.READY,
        watched=DOWNLOADS,
    )


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (episode(state=MatchState.UNCERTAIN), "TMDB isn't sure which one it is."),
        (episode(state=MatchState.NOT_FOUND), "Not found at TMDB."),
        (episode(state=MatchState.OFFLINE), "No TMDB key, or TMDB can't be reached."),
        (episode(state=MatchState.SKIPPED), "Skipped before."),
        (movie(year=None), "No year in the name: is it a movie?"),
        (movie(year=2021, tmdb_year=1984), "The name and TMDB don't agree."),
        (episode(number=None), "No season or episode number in the name."),
        (episode(known=False), "TMDB doesn't know this episode."),
        (episode(tmdb_title="Something Else"), "The name and TMDB don't agree."),
    ],
)
def test_not_sure_gives_one_reason(item: QueueItem, reason: str) -> None:
    assert decide(item) == (False, reason)


def test_a_remembered_match_is_sure() -> None:
    # The owner confirmed this title before: even a movie without a year in its name.
    assert decide(movie(year=None, state=MatchState.REMEMBERED)).sure


def test_a_confident_episode_is_sure() -> None:
    assert decide(episode()) == (True, "")
    # TMDB lists it under another name, but its original title is the file's.
    assert decide(
        episode("La Casa de Papel", tmdb_title="Money Heist", original="La Casa de Papel")
    ).sure


def test_a_confident_movie_with_its_year_is_sure() -> None:
    assert decide(movie()) == (True, "")


def test_shows_and_movies_go_to_their_own_folder_with_their_own_preset() -> None:
    runs = library_runs(
        [episode(item_id=1), movie(item_id=2)], SETTINGS, DEFAULT_PRESETS, lambda p: False
    )
    by_kind = {r.kind: r for r in runs}
    assert by_kind[MediaKind.EPISODE].preset.name == "Series (Folder)"
    assert by_kind[MediaKind.MOVIE].preset.name == "Movie (Folder)"
    [show] = by_kind[MediaKind.EPISODE].plan.files
    [film] = by_kind[MediaKind.MOVIE].plan.files
    assert show.target == SERIES / "VisionQuest (2025)" / "VisionQuest (2025) - Season 01" / (
        "VisionQuest (2025) - S01E02 - Theseus.mkv"
    )
    assert film.target == MOVIES / "Dune (2021)" / "Dune (2021).mkv"
    for run in runs:  # the preset's posters stay; the dropped-file clean-up options don't apply
        assert not run.plan.options.clean_junk and not run.plan.options.prune_empty
        assert run.held == {}


def test_an_existing_show_folder_is_reused() -> None:
    existing = SERIES / "VisionQuest (2025)" / "VisionQuest (2025) - Season 01"
    [run] = library_runs([episode()], SETTINGS, DEFAULT_PRESETS, lambda p: p == existing)
    assert run.plan.files[0].target.parent == existing


def test_already_in_the_library_is_held_not_numbered() -> None:
    wanted = MOVIES / "Dune (2021)" / "Dune (2021).mkv"
    [run] = library_runs([movie()], SETTINGS, DEFAULT_PRESETS, lambda p: p == wanted)
    assert run.plan.files == ()
    assert run.held == {1: "Already in the library."}


def test_an_owner_click_may_number_a_copy() -> None:
    wanted = MOVIES / "Dune (2021)" / "Dune (2021).mkv"
    [run] = owner_runs([movie()], SETTINGS, DEFAULT_PRESETS, lambda p: p == wanted)
    assert run.plan.files[0].target == wanted.with_name("Dune (2021) (1).mkv")
    assert run.held == {}


def test_a_missing_preset_falls_back_to_the_built_in_one() -> None:
    renamed = SETTINGS.model_copy(update={"movies_preset": "Gone"})
    [run] = library_runs([movie()], renamed, DEFAULT_PRESETS[:1], lambda p: False)
    assert run.preset.name == "Movie (Folder)"


def test_a_target_outside_the_library_is_refused() -> None:
    stray = movie()
    stray.watched = Path(r"C:\elsewhere")  # not where the file is
    with pytest.raises(ValueError, match="outside"):
        library_runs([stray], SETTINGS, DEFAULT_PRESETS, lambda p: False)
