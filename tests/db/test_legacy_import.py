import json
from pathlib import Path

import pytest

from mediaforge.db.database import Database
from mediaforge.db.history import HistoryRepo
from mediaforge.db.legacy_import import find_legacy, import_legacy
from mediaforge.db.presets import PresetRepo
from mediaforge.db.settings import AppSettings, SettingsRepo
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import TypeHint

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def legacy_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "mediaforge_config.json").write_bytes((FIXTURES / "legacy_config.json").read_bytes())
    undo = (FIXTURES / "legacy_undo.json").read_text(encoding="utf-8")
    root = json.dumps(str(tmp_path / "media"))[1:-1]
    (folder / "mediaforge_undo.json").write_text(undo.replace("{ROOT}", root), encoding="utf-8")
    return folder


async def run_import(
    db: Database, legacy_dir: Path
) -> tuple[SettingsRepo, PresetRepo, HistoryRepo]:
    settings, presets, history = SettingsRepo(db), PresetRepo(db), HistoryRepo(db)
    config, undo = find_legacy([legacy_dir.parent, legacy_dir])
    report = await import_legacy(config, undo, settings, presets, history)
    assert report.presets == 5 and report.key_imported and report.history_files == 2
    return settings, presets, history


def test_find_legacy(legacy_dir: Path, tmp_path: Path) -> None:
    assert find_legacy([tmp_path, legacy_dir]) == (
        legacy_dir / "mediaforge_config.json",
        legacy_dir / "mediaforge_undo.json",
    )
    assert find_legacy([tmp_path]) == (None, None)


async def test_import_presets_options_mapping(db: Database, legacy_dir: Path) -> None:
    _, presets, _ = await run_import(db, legacy_dir)
    by_name = {p.name: p for p in await presets.list()}
    assert list(by_name) == [
        "Auto",
        "Movie (Folder)",
        "Movie (Flat)",
        "Series (Folder)",
        "Series (Flat)",
    ]
    series = by_name["Series (Folder)"]
    assert series.type is TypeHint.TV
    assert series.options.series_poster and series.options.season_poster
    assert not series.options.movie_poster
    assert (
        by_name["Movie (Folder)"].options.movie_poster and by_name["Movie (Folder)"].options.trailer
    )


async def test_import_sets_key_theme_geometry_and_flag(db: Database, legacy_dir: Path) -> None:
    settings, _, _ = await run_import(db, legacy_dir)
    s = await settings.load()
    assert (s.tmdb_key, s.theme, s.window_geometry, s.legacy_imported) == (
        "dummy-key-0000",
        "Light",
        "1200x850+38+38",
        True,
    )


async def test_import_keeps_a_key_already_set(db: Database, legacy_dir: Path) -> None:
    settings = SettingsRepo(db)
    await settings.save(AppSettings(tmdb_key="newer-key"))
    config, undo = find_legacy([legacy_dir])
    report = await import_legacy(config, undo, settings, PresetRepo(db), HistoryRepo(db))
    assert not report.key_imported
    assert (await settings.load()).tmdb_key == "newer-key"


async def test_import_undo_becomes_one_run(db: Database, legacy_dir: Path, tmp_path: Path) -> None:
    _, _, history = await run_import(db, legacy_dir)
    [run] = await history.list_runs()
    assert run.source is RunSource.IMPORT and run.file_count == 2 and run.undoable_count == 2
    ops = await history.operations(run.id)
    assert [o.kind for o in ops] == [OpKind.RENAME, OpKind.RENAME]
    assert ops[0].dst == str(tmp_path / "media" / "VisionQuest (2026) - S01E02 - Theseus.mkv")


async def test_import_is_one_time(db: Database, legacy_dir: Path) -> None:
    settings, presets, history = await run_import(db, legacy_dir)
    config, undo = find_legacy([legacy_dir])
    again = await import_legacy(config, undo, settings, presets, history)
    assert (again.presets, again.key_imported, again.history_files) == (0, False, 0)
    assert len(await history.list_runs()) == 1


async def test_force_imports_again(db: Database, legacy_dir: Path) -> None:
    settings, presets, history = SettingsRepo(db), PresetRepo(db), HistoryRepo(db)
    await import_legacy(None, None, settings, presets, history)  # first start found nothing
    config, undo = find_legacy([legacy_dir])
    report = await import_legacy(config, None, settings, presets, history, force=True)
    assert report.presets == 5


async def test_legacy_files_untouched(db: Database, legacy_dir: Path) -> None:
    before = {p.name: p.read_bytes() for p in legacy_dir.iterdir()}
    await run_import(db, legacy_dir)
    assert {p.name: p.read_bytes() for p in legacy_dir.iterdir()} == before


async def test_import_without_files_still_marks_done(db: Database) -> None:
    settings = SettingsRepo(db)
    report = await import_legacy(None, None, settings, PresetRepo(db), HistoryRepo(db))
    assert report.presets == 0
    assert (await settings.load()).legacy_imported
