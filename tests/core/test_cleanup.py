from pathlib import Path

from mediaforge.core.cleanup import find_cleanup_targets


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_junk_files_anywhere(tmp_path: Path) -> None:
    a = touch(tmp_path / "Thumbs.db")
    b = touch(tmp_path / "sub" / ".DS_Store")
    c = touch(tmp_path / "sub" / "DESKTOP.INI")
    assert set(find_cleanup_targets(tmp_path)) == {a, b, c}


def test_url_with_matching_video_is_removed(tmp_path: Path) -> None:
    url = touch(tmp_path / "Dune (2021) - Trailer.url")
    touch(tmp_path / "Dune (2021) - Trailer.MP4")
    keep = touch(tmp_path / "Other - Trailer.url")
    assert find_cleanup_targets(tmp_path) == [url]
    assert keep.exists()


def test_orphan_movie_poster_removed(tmp_path: Path) -> None:
    orphan = touch(tmp_path / "Old Name (2020) - Poster.jpg")
    assert find_cleanup_targets(tmp_path) == [orphan]


def test_cleanup_keeps_poster_with_matching_video(tmp_path: Path) -> None:
    touch(tmp_path / "Dune (2021) - Poster.jpg")
    touch(tmp_path / "Dune (2021).mkv")
    assert find_cleanup_targets(tmp_path) == []


def test_series_poster_removed_only_when_tree_has_no_videos(tmp_path: Path) -> None:
    kept = touch(tmp_path / "Show" / "Show - Series Poster.jpg")
    touch(tmp_path / "Show" / "Season 01" / "Show - S01E01.mkv")
    gone = touch(tmp_path / "Empty" / "Empty - Series Poster.jpg")
    assert find_cleanup_targets(tmp_path) == [gone]
    assert kept.exists()


def test_season_poster_removed_when_folder_has_no_videos(tmp_path: Path) -> None:
    touch(tmp_path / "S1" / "Season 01 Poster.jpg")
    touch(tmp_path / "S1" / "ep.avi")
    gone = touch(tmp_path / "S2" / "Season 02 Poster.jpg")
    touch(tmp_path / "S2" / "deeper" / "ep.mkv")  # videos in a subfolder don't count
    assert find_cleanup_targets(tmp_path) == [gone]


def test_ordinary_files_untouched(tmp_path: Path) -> None:
    touch(tmp_path / "cover.jpg")
    touch(tmp_path / "notes.txt")
    touch(tmp_path / "movie.mkv")
    assert find_cleanup_targets(tmp_path) == []


def test_download_leftovers_are_samples_and_notes_only(tmp_path: Path) -> None:
    from mediaforge.core.cleanup import download_leftovers

    release = tmp_path / "Show.S01E01.1080p-GRP"
    junk = {
        touch(release / "Sample" / "show.s01e01-sample.mkv"),
        touch(release / "show.nfo"),
        touch(release / "RARBG.txt"),
        touch(release / "Visit.url"),
        touch(release / "show.sfv"),
        touch(release / "Thumbs.db"),
    }
    touch(release / "show.s01e01.en.srt")  # subtitles stay
    touch(release / "Screens" / "shot1.jpg")  # pictures stay
    touch(release / "show.s01e01.mkv")  # the video itself is never a leftover
    assert set(download_leftovers(release)) == junk
