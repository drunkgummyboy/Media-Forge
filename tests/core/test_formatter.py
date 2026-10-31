from typing import Any

import pytest

from mediaforge.core.formatter import render, sanitize_segment
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia, TypeHint
from mediaforge.models.preset import DEFAULT_PRESETS, LEGACY_OPTION_KEYS

SERIES_FOLDER = "{ny}/{ny} - Season {s}/{ny} - {s00e00} - {en}"


def ep(**kw: Any) -> ResolvedMedia:
    parsed_fields: dict[str, Any] = {
        "source_name": "x.mkv",
        "kind": MediaKind.EPISODE,
        "title": "VisionQuest",
        "season": kw.pop("season", 1),
        "episode": kw.pop("episode", 2),
        "episode_end": kw.pop("episode_end", None),
    }
    parsed = ParsedMedia(**parsed_fields)
    return ResolvedMedia(
        parsed=parsed,
        title=kw.pop("title", "VisionQuest"),
        year=kw.pop("year", 2026),
        episode_title=kw.pop("episode_title", "Theseus"),
        match_state=MatchState.CONFIDENT,
    )


def movie(**kw: Any) -> ResolvedMedia:
    parsed = ParsedMedia(source_name="x.mkv", kind=MediaKind.MOVIE, title="Dune Part Two")
    return ResolvedMedia(
        parsed=parsed,
        title=kw.pop("title", "Dune Part Two"),
        year=kw.pop("year", 2024),
        episode_title=None,
        match_state=MatchState.CONFIDENT,
    )


def test_series_folder_preset() -> None:
    r = render(SERIES_FOLDER, ep(), ".MKV")
    assert r.parts == (
        "VisionQuest (2026)",
        "VisionQuest (2026) - Season 01",
        "VisionQuest (2026) - S01E02 - Theseus.mkv",
    )
    assert str(r.relative) == "\\".join(r.parts)


def test_variables_case_insensitive() -> None:
    assert render("{NY}", movie(), ".mp4").parts == ("Dune Part Two (2024).mp4",)


def test_t_s_e_variables() -> None:
    assert render("{t} s{s} e{e}", ep(), ".mkv").parts == ("VisionQuest s01 e02.mkv",)


def test_multi_episode() -> None:
    assert render("{s00e00}", ep(episode=1, episode_end=2), ".mkv").parts == ("S01E01-E02.mkv",)


def test_missing_values() -> None:
    r = render("{t} {s00e00} {en}", ep(season=None, episode=None, episode_title=None), ".mkv")
    assert r.parts == ("VisionQuest S00E00 Episode 00.mkv",)


def test_no_year() -> None:
    assert render("{ny}", movie(year=None), ".mkv").parts == ("Dune Part Two.mkv",)


def test_values_are_not_rescanned() -> None:
    r = render("{en}", ep(episode_title="{t}"), ".mkv")
    assert r.parts == ("{t}.mkv",)
    assert r.unknown == ()


def test_unknown_variable_kept_and_reported() -> None:
    r = render("{ny} {foo}", movie(), ".mkv")
    assert r.parts == ("Dune Part Two (2024) {foo}.mkv",)
    assert r.unknown == ("foo",)


def test_backslash_and_slash_both_split() -> None:
    assert len(render("{t}\\{t}/{t}", movie(), ".mkv").parts) == 3


def test_empty_segments_dropped() -> None:
    assert render("/{t}//", movie(), ".mkv").parts == ("Dune Part Two.mkv",)


def test_empty_format_gives_placeholder_name() -> None:
    assert render("", movie(), ".mkv").parts == ("_.mkv",)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('Who: Are "You"?', "Who Are You"),
        ("Part 1...", "Part 1"),
        ("  a   b  ", "a b"),
        ("CON", "CON_"),
        ("nul", "nul_"),
        ("COM1", "COM1_"),
        ("", "_"),
        ("a\x07b", "ab"),
        ("ｆｕｌｌ", "full"),
        ("a<b>c|d*e", "abcde"),
    ],
)
def test_sanitize_segment(raw: str, clean: str) -> None:
    assert sanitize_segment(raw) == clean


def test_reserved_name_check_applies_to_whole_segment_only() -> None:
    assert render("{t}", movie(title="Con Air", year=None), ".mkv").parts == ("Con Air.mkv",)
    assert render("{t}", movie(title="CON", year=None), ".mkv").parts == ("CON_.mkv",)


def test_episode_title_with_slash_does_not_create_folder() -> None:
    assert render("{en}", ep(episode_title="AC/DC"), ".mkv").parts == ("ACDC.mkv",)


def test_trailing_dot_in_episode_title_is_stripped() -> None:
    assert render("{en}", ep(episode_title="To Be Continued..."), ".mkv").parts == (
        "To Be Continued.mkv",
    )


def test_default_presets() -> None:
    assert [p.name for p in DEFAULT_PRESETS] == [
        "Auto",
        "Movie (Folder)",
        "Movie (Flat)",
        "Series (Folder)",
        "Series (Flat)",
    ]
    by_name = {p.name: p for p in DEFAULT_PRESETS}
    assert by_name["Series (Folder)"].format == SERIES_FOLDER
    assert by_name["Series (Folder)"].type is TypeHint.TV
    assert by_name["Series (Folder)"].options.series_poster
    assert by_name["Series (Folder)"].options.season_poster
    assert by_name["Movie (Folder)"].options.movie_poster
    assert by_name["Movie (Folder)"].options.trailer
    assert by_name["Auto"].format == "{ny}"


def test_legacy_option_keys() -> None:
    assert LEGACY_OPTION_KEYS["chk_poster"] == "movie_poster"
    assert LEGACY_OPTION_KEYS["chk_prune"] == "prune_empty"
    assert "chk_recursive" not in LEGACY_OPTION_KEYS


def test_resolved_media_offline_copies_parsed() -> None:
    parsed = ParsedMedia(
        source_name="a.mkv",
        kind=MediaKind.EPISODE,
        title="Show",
        year=2001,
        season=2,
        episode=3,
        episode_title="Pilot",
    )
    r = ResolvedMedia.offline(parsed)
    assert (r.title, r.year, r.episode_title, r.match_state) == (
        "Show",
        2001,
        "Pilot",
        MatchState.OFFLINE,
    )
    assert (r.kind, r.season, r.episode, r.ny) == (MediaKind.EPISODE, 2, 3, "Show (2001)")
