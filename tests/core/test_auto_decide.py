"""When a download may be renamed on its own, and where it goes."""

from pathlib import Path

import pytest

from mediaforge.core.auto import SAME_NAME, decide, library_runs, owner_runs
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


def test_a_target_outside_the_library_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Presets can't escape the library (names are cleaned), so the last guard is shown by hand.
    from mediaforge.core import auto
    from mediaforge.models.plan import Plan, PlannedFile

    item = movie()
    stray = PlannedFile(item_id=1, source=item.path, target=Path(r"C:\elsewhere\x.mkv"))
    monkeypatch.setattr(auto, "build_plan", lambda *args, **kwargs: Plan(files=(stray,)))
    with pytest.raises(ValueError, match="outside"):
        owner_runs([item], SETTINGS, DEFAULT_PRESETS, lambda p: False)


def test_a_moved_files_release_folder_is_planned_for_clean_up() -> None:
    [run] = library_runs([episode()], SETTINGS, DEFAULT_PRESETS, lambda p: False)
    [leftovers] = run.plan.leftovers
    assert leftovers.folder == DOWNLOADS / "VisionQuest.S01E02" and leftovers.item_ids == (1,)


def test_the_watched_folder_itself_is_never_removed() -> None:
    [run] = library_runs([movie()], SETTINGS, DEFAULT_PRESETS, lambda p: False)  # loose in it
    assert run.plan.leftovers == ()


def test_no_leftover_plan_when_clean_downloads_is_off() -> None:
    off = SETTINGS.model_copy(update={"clean_downloads": False})
    [run] = library_runs([episode()], off, DEFAULT_PRESETS, lambda p: False)
    assert run.plan.leftovers == ()


def test_a_held_file_keeps_its_folder() -> None:
    taken = SERIES / "VisionQuest (2025)" / "VisionQuest (2025) - Season 01"
    target = taken / "VisionQuest (2025) - S01E02 - Theseus.mkv"
    [run] = library_runs([episode()], SETTINGS, DEFAULT_PRESETS, lambda p: p == target)
    assert run.plan.leftovers == ()


def test_a_remembered_episode_still_needs_its_numbers() -> None:
    # A remembered match vouches for the show, not for the episode.
    assert decide(episode(number=None, state=MatchState.REMEMBERED)) == (
        False,
        "No season or episode number in the name.",
    )
    assert decide(episode(known=False, state=MatchState.REMEMBERED)) == (
        False,
        "TMDB doesn't know this episode.",
    )


def test_two_downloads_wanting_one_name_both_wait() -> None:
    first, second = movie(item_id=1), movie(item_id=2)
    second.path = DOWNLOADS / "Dune.2021.Remux" / "Dune.2021.Remux.mkv"
    [run] = library_runs([first, second], SETTINGS, DEFAULT_PRESETS, lambda p: False)
    assert run.plan.files == ()
    assert run.held == {1: SAME_NAME, 2: SAME_NAME}


def test_the_same_title_with_another_extension_is_already_there() -> None:
    other = MOVIES / "Dune (2021)" / "Dune (2021).mp4"
    [run] = library_runs([movie()], SETTINGS, DEFAULT_PRESETS, lambda p: p == other)
    assert run.held == {1: "Already in the library."}


def test_a_category_folder_is_never_cleaned() -> None:
    loose = movie()
    loose.path = DOWNLOADS / "Movies" / "Dune.2021.mkv"  # a downloader's category folder
    [run] = library_runs([loose], SETTINGS, DEFAULT_PRESETS, lambda p: False)
    assert run.plan.leftovers == ()
    release = movie()
    release.path = DOWNLOADS / "Dune.2021.2160p.WEB-DL" / "Dune.2021.mkv"
    [run] = library_runs([release], SETTINGS, DEFAULT_PRESETS, lambda p: False)
    assert [lo.folder for lo in run.plan.leftovers] == [release.path.parent]


def test_a_file_outside_its_watched_folder_waits() -> None:
    stray = movie()
    stray.watched = Path(r"C:\elsewhere")  # not where the file is any more
    [run] = library_runs([stray], SETTINGS, DEFAULT_PRESETS, lambda p: False)
    assert run.plan.files == () and run.held == {1: "It isn't in a watched folder any more."}
