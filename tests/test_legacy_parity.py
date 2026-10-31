"""Every capability of the legacy MediaForge.py (spec Appendix A) is pinned by a test.

Each entry names the tests that prove it. This file checks those tests exist, so the
mapping can't silently rot; docs/superpowers/verification/ lists the manual checks.
"""

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

PARITY: dict[str, list[str]] = {
    "01 video extensions": [
        "core/test_parser.py::test_is_video_case_insensitive",
        "core/test_queue.py::test_non_videos_ignored",
    ],
    "02 add, clear, remove": [
        "ui/test_shell.py::test_add_paths_queues_files",
        "core/test_queue.py::test_remove_and_clear",
    ],
    "03 recursive scan + rescan": [
        "core/test_queue.py::test_add_folder_recursive_and_flat",
        "core/test_queue.py::test_rescan_after_toggle",
    ],
    "04 presets": [
        "core/test_formatter.py::test_default_presets",
        "db/test_repos.py::test_cannot_delete_last_preset",
        "ui/test_settings_view.py::test_save_preset_updates_queue_dropdown",
    ],
    "05 live format preview": [
        "ui/test_queue_view.py::test_format_change_updates_titles",
        "ui/test_queue_view.py::test_unknown_variable_flagged",
    ],
    "06 grouped queue with status": [
        "ui/test_queue_view.py::test_queue_view_lists_added_files",
        "ui/test_queue_view.py::test_forge_renames_and_shows_done",
    ],
    "07 preview panel": ["ui/test_detail_panel.py::test_detail_panel_shows_names_and_episode"],
    "08 auto type + override": [
        "core/test_parser.py::test_hint_movie_forces_movie_kind",
        "core/test_queue.py::test_set_hint_reparses_all",
    ],
    "09 TMDB search, confidence, picker": [
        "api/test_tmdb.py::test_movie_search_with_year_then_without_dedupes_in_order",
        "core/test_matcher.py::test_choose_confident_on_title_and_year",
        "core/test_matcher.py::test_choose_single_result_confident",
        "core/test_queue.py::test_choose_match_applies_to_same_key",
    ],
    "10 episode names": [
        "core/test_matcher.py::test_resolve_fetches_episode_title_and_season_poster",
        "core/test_formatter.py::test_missing_values",
    ],
    "11 multi-episode": ["core/test_formatter.py::test_multi_episode"],
    "12 rename and move": [
        "core/test_planner.py::test_collision_with_existing_file_gets_suffix",
        "core/test_planner.py::test_already_named_is_skipped",
        "core/test_executor.py::test_rename_run_journals_everything",
    ],
    "13 posters": [
        "core/test_planner.py::test_series_and_season_posters_deduplicated",
        "core/test_executor.py::test_poster_not_overwritten",
    ],
    "14 trailers": [
        "api/test_trailers.py::test_download_success_returns_mp4",
        "api/test_trailers.py::test_download_failure_writes_url_shortcut",
    ],
    "15 clean junk": [
        "core/test_cleanup.py::test_junk_files_anywhere",
        "core/test_cleanup.py::test_orphan_movie_poster_removed",
    ],
    "16 prune empty folders": ["core/test_executor.py::test_order_clean_then_files_then_prune"],
    "17 undo": [
        "core/test_undo.py::test_undo_whole_run_restores_names_and_removes_created_dirs_and_posters",
        "ui/test_history_view.py::test_undo_run_button_reverts_files",
    ],
    "18 settings": [
        "ui/test_settings_view.py::test_key_check_uses_validate",
        "ui/test_settings_view.py::test_theme_saved",
        "ui/test_settings_view.py::test_context_menu_buttons_call_install_with_entries",
    ],
    "19 window size remembered": ["ui/test_shell.py::test_geometry_saved"],
    "20 command line": [
        "platform/test_cli.py::test_parse_paths_preset_confirm",
        "platform/test_cli.py::test_auto_run_means_confirm",
    ],
    "21 shortcuts": [
        "ui/test_queue_view.py::test_shortcuts_ignored_in_entry",
        "ui/test_queue_view.py::test_shortcut_actions",
    ],
    "22 coloured activity log": [
        "ui/test_shell.py::test_log_strip_shows_records",
        "ui/test_shell.py::test_log_strip_colours",
    ],
    "23 icon and logo": ["test_no_secrets.py::test_assets_ship_inside_the_package"],
}


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    }


def test_all_23_legacy_capabilities_are_listed() -> None:
    assert len(PARITY) == 23


@pytest.mark.parametrize("capability", sorted(PARITY))
def test_every_capability_points_at_real_tests(capability: str) -> None:
    for ref in PARITY[capability]:
        file, name = ref.split("::")
        assert name in _test_names(TESTS / file), f"{capability}: missing {ref}"
