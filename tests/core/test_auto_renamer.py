"""Renaming on its own: finished downloads are looked up, decided on, and moved into the library."""

import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from mediaforge.app import Services, build_services, close_services
from mediaforge.core.auto import AutoRenamer, needs_message, summary_due, summary_message
from mediaforge.core.queue import ItemStatus
from mediaforge.models.history import RunSource
from mediaforge.models.media import MediaKind
from mediaforge.models.tmdb import TmdbEpisode, TmdbResult, TmdbSeason
from mediaforge.platform.paths import app_paths


class Tmdb:
    """TMDB as far as the matcher and the poster download need it."""

    async def search_tv(self, query: str, year: int | None) -> list[TmdbResult]:
        return [TmdbResult(id=7, title="VisionQuest", date="2025-01-10", poster_path="/7.jpg")]

    async def search_movie(self, query: str, year: int | None) -> list[TmdbResult]:
        return [TmdbResult(id=8, title=query, date="2021-09-15", poster_path="/8.jpg")]

    async def details(self, kind: MediaKind, tmdb_id: int) -> TmdbResult | None:
        return None

    async def season(self, tv_id: int, number: int) -> TmdbSeason | None:
        names = {1: "No Strings", 2: "Theseus"}
        episodes = [TmdbEpisode(episode_number=n, name=name) for n, name in names.items()]
        return TmdbSeason(season_number=number, poster_path="/s1.jpg", episodes=episodes)

    async def image(self, path: str, size: str) -> bytes:
        return b"jpeg"


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class Folders:
    def __init__(self, root: Path) -> None:
        self.downloads = root / "Downloads"
        self.series = root / "Library" / "Series"
        self.movies = root / "Library" / "Movies"
        for folder in (self.downloads, self.series, self.movies):
            folder.mkdir(parents=True)

    def download(self, name: str, age_s: float = 3600) -> Path:
        release = self.downloads / Path(name).stem
        release.mkdir(exist_ok=True)
        file = release / name
        file.write_bytes(b"video")
        when = time.time() - age_s
        os.utime(file, (when, when))
        return file


@pytest_asyncio.fixture
async def services(mediaforge_home: Path) -> AsyncIterator[Services]:
    paths = app_paths()
    paths.ensure()
    s = await build_services(paths, legacy_dirs=[])
    fake = Tmdb()
    s.resolver.set_client(fake)  # type: ignore[arg-type]
    s.tmdb_ref.client = fake  # type: ignore[assignment]
    try:
        yield s
    finally:
        await close_services(s)


@pytest.fixture
def folders(tmp_path: Path) -> Folders:
    return Folders(tmp_path)


def use(services: Services, folders: Folders, automatic: str = "on") -> None:
    services.app_settings = services.app_settings.model_copy(
        update={
            "watch_folders": [str(folders.downloads)],
            "series_folder": str(folders.series),
            "movies_folder": str(folders.movies),
            "automatic": automatic,
        }
    )


async def two_looks(auto: AutoRenamer, clock: Clock) -> None:
    await auto.look()  # first sight
    clock.now += 300
    await auto.look()  # unchanged since: finished


def renamer(
    services: Services, clock: Clock, notes: list[tuple[str, str]] | None = None
) -> AutoRenamer:
    auto = AutoRenamer(services, now=clock)
    if notes is not None:
        auto.notify = lambda title, text: notes.append((title, text))
    return auto


async def test_a_sure_episode_is_moved_into_the_series_folder(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    folders.download("VisionQuest.S01E02.Theseus.1080p.WEB.mkv")
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    season = folders.series / "VisionQuest (2025)" / "VisionQuest (2025) - Season 01"
    assert (season / "VisionQuest (2025) - S01E02 - Theseus.mkv").exists()
    assert not any(folders.downloads.iterdir())  # moved, and its release folder cleared
    [item] = services.queue.items
    assert item.status is ItemStatus.DONE and item.run_id is not None


async def test_a_sure_movie_is_moved_into_the_movies_folder(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    assert (folders.movies / "Dune (2021)" / "Dune (2021).mkv").exists()


async def test_trial_moves_nothing(services: Services, folders: Folders) -> None:
    use(services, folders, automatic="trial")
    file = folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    assert file.exists()
    [item] = services.queue.items
    assert item.status is ItemStatus.READY and item.watched == folders.downloads


async def test_an_unsure_match_waits_with_its_reason(services: Services, folders: Folders) -> None:
    use(services, folders)
    file = folders.download("Dune.mkv")  # no year: is it a movie?
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    assert file.exists()
    [item] = services.queue.items
    assert item.status is ItemStatus.HELD
    assert item.message == "No year in the name: is it a movie?"


async def test_a_file_still_coming_in_waits(services: Services, folders: Folders) -> None:
    use(services, folders)
    folders.download("Dune.2021.2160p.WEB-DL.mkv", age_s=60)  # written a minute ago
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    assert services.queue.items == []


async def test_an_unreachable_library_holds_and_retries(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    folders.download("Dune.2021.2160p.WEB-DL.mkv")
    folders.movies.rmdir()  # the NAS share is away
    clock = Clock()
    auto = renamer(services, clock)
    statuses: list[str] = []
    auto.on_status = lambda status: statuses.append(status.state)
    await two_looks(auto, clock)
    [item] = services.queue.items
    assert item.status is ItemStatus.HELD and "Can't reach" in item.message
    assert statuses[-1] == "unreachable"
    folders.movies.mkdir()  # back again
    clock.now += 300
    await auto.look()
    assert (folders.movies / "Dune (2021)" / "Dune (2021).mkv").exists()
    assert statuses[-1] == "watching"


async def test_an_unreachable_download_folder_changes_the_status(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    folders.downloads.rmdir()
    auto = renamer(services, Clock())
    statuses: list[tuple[str, str]] = []
    auto.on_status = lambda status: statuses.append((status.state, status.text))
    result = await auto.look()
    assert result.unreachable == [folders.downloads]
    assert statuses[-1] == ("unreachable", f"Can't reach {folders.downloads}")


async def test_an_undone_file_is_not_moved_again(services: Services, folders: Folders) -> None:
    use(services, folders)
    file = folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    auto = renamer(services, clock)
    await two_looks(auto, clock)
    [item] = services.queue.items
    assert item.run_id is not None
    await services.undo.undo_run(item.run_id)
    assert file.exists()  # back in the download folder
    clock.now += 300
    await two_looks(auto, clock)
    assert file.exists()
    held = [i for i in services.queue.items if i.status is ItemStatus.HELD]
    assert [i.message for i in held] == ["You undid this once; click Rename to move it again."]


async def test_files_in_a_library_folder_are_ignored(services: Services, folders: Folders) -> None:
    use(services, folders)
    overlapping = str(folders.series.parent)  # watching the library itself
    services.app_settings = services.app_settings.model_copy(
        update={"watch_folders": [overlapping]}
    )
    done = folders.series / "Show (2020)" / "Show (2020) - S01E01.mkv"
    done.parent.mkdir(parents=True)
    done.write_bytes(b"video")
    os.utime(done, (time.time() - 3600, time.time() - 3600))
    clock = Clock()
    auto = renamer(services, clock)
    statuses: list[str] = []
    auto.on_status = lambda status: statuses.append(status.state)
    await two_looks(auto, clock)
    assert services.queue.items == [] and done.exists()
    assert statuses[-1] == "setup"


async def test_one_automatic_run_per_kind_per_look(services: Services, folders: Folders) -> None:
    use(services, folders)
    folders.download("VisionQuest.S01E01.No.Strings.1080p.WEB.mkv")
    folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    await two_looks(renamer(services, clock), clock)
    runs = await services.history.list_runs()
    assert sorted(tuple(r.kinds) for r in runs) == [(MediaKind.EPISODE,), (MediaKind.MOVIE,)]
    assert {r.source for r in runs} == {RunSource.AUTOMATIC}


async def test_the_needs_notification_comes_once_per_batch(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    folders.download("Dune.mkv")
    folders.download("Heat.mkv")
    clock = Clock()
    notes: list[tuple[str, str]] = []
    auto = renamer(services, clock, notes)
    await two_looks(auto, clock)
    clock.now += 300
    await auto.look()  # nothing new
    assert notes == [("2 files need you", "Open MediaForge to pick or confirm them.")]


def test_summary_message_texts() -> None:
    assert summary_message(5, 1) == "Today: 5 episodes and 1 movie renamed"
    assert summary_message(1, 0) == "Today: 1 episode renamed"
    assert summary_message(0, 3) == "Today: 3 movies renamed"
    assert needs_message(1) == ("1 file needs you", "Open MediaForge to pick or confirm them.")


def test_summary_due_once_a_day_after_eight() -> None:
    evening = datetime(2026, 11, 2, 20, 5)
    assert not summary_due(datetime(2026, 11, 2, 19, 59), "")
    assert summary_due(evening, "")
    assert summary_due(evening, "2026-11-01")
    assert not summary_due(evening, "2026-11-02")  # sent today already


async def test_the_evening_summary_counts_todays_automatic_renames(
    services: Services, folders: Folders
) -> None:
    use(services, folders)
    file = folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    clock.now = datetime.now().replace(hour=20, minute=30).timestamp()
    os.utime(file, (clock.now - 3600, clock.now - 3600))  # finished an hour before "now"
    notes: list[tuple[str, str]] = []
    auto = renamer(services, clock, notes)
    await two_looks(auto, clock)
    clock.now += 300
    await auto.look()
    assert notes == [("MediaForge", "Today: 1 movie renamed")]
    assert services.app_settings.summary_sent_on == datetime.now().date().isoformat()
    clock.now += 300
    await auto.look()
    assert len(notes) == 1  # once a day


async def test_unreachable_is_logged_once(
    services: Services, folders: Folders, caplog: pytest.LogCaptureFixture
) -> None:
    use(services, folders)
    folders.downloads.rmdir()
    clock = Clock()
    auto = renamer(services, clock)
    with caplog.at_level(logging.INFO, logger="mediaforge"):
        for _ in range(3):
            await auto.look()
            clock.now += 300
        folders.downloads.mkdir()
        await auto.look()
        await auto.look()
    lost = [r for r in caplog.records if r.getMessage() == f"Can't reach {folders.downloads}"]
    back = [r for r in caplog.records if r.getMessage() == f"Can reach {folders.downloads} again"]
    assert len(lost) == 1 and len(back) == 1


async def test_done_today_rolls_over_at_midnight(services: Services, folders: Folders) -> None:
    use(services, folders)
    folders.download("Dune.2021.2160p.WEB-DL.mkv")
    clock = Clock()
    auto = renamer(services, clock)
    await two_looks(auto, clock)
    assert [i.status for i in services.queue.items] == [ItemStatus.DONE]
    clock.now += 24 * 3600
    await auto.look()
    assert services.queue.items == []


async def test_rename_by_the_owner_moves_a_held_file(services: Services, folders: Folders) -> None:
    use(services, folders)
    folders.download("Dune.mkv")
    clock = Clock()
    auto = renamer(services, clock)
    await two_looks(auto, clock)
    [item] = services.queue.items
    assert item.status is ItemStatus.HELD
    result = await auto.rename([item.id], source=RunSource.APP)
    assert result.renamed == 1
    assert (folders.movies / "Dune (2021)" / "Dune (2021).mkv").exists()
    assert item.status is ItemStatus.DONE
