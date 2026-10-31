import pytest

from mediaforge.db import cache as cache_module
from mediaforge.db.cache import CacheRepo
from mediaforge.db.database import Database
from mediaforge.db.matches import MatchRepo, RememberedMatch
from mediaforge.db.presets import LastPresetError, PresetRepo
from mediaforge.db.settings import AppSettings, SettingsRepo
from mediaforge.models.media import MediaKind, TypeHint
from mediaforge.models.preset import Preset, PresetOptions

DEFAULT_NAMES = ["Auto", "Movie (Folder)", "Movie (Flat)", "Series (Folder)", "Series (Flat)"]


async def test_settings_roundtrip_and_defaults(db: Database) -> None:
    repo = SettingsRepo(db)
    assert await repo.load() == AppSettings()
    changed = AppSettings(
        tmdb_key="k", theme="Light", scan_subfolders=False, context_menu_presets=["Auto"]
    )
    await repo.save(changed)
    assert await repo.load() == changed


async def test_presets_seed_defaults_order(db: Database) -> None:
    repo = PresetRepo(db)
    await repo.seed_defaults_if_empty()
    await repo.seed_defaults_if_empty()  # second call is a no-op
    assert [p.name for p in await repo.list()] == DEFAULT_NAMES


async def test_preset_save_new_goes_last_and_overwrite_keeps_place(db: Database) -> None:
    repo = PresetRepo(db)
    await repo.seed_defaults_if_empty()
    await repo.save(Preset(name="Mine", type=TypeHint.TV, format="{t}"))
    await repo.save(Preset(name="Auto", type=TypeHint.AUTO, format="{t}"))
    presets = await repo.list()
    assert [p.name for p in presets] == [*DEFAULT_NAMES, "Mine"]
    assert presets[0].format == "{t}"


async def test_preset_rename_keeps_position(db: Database) -> None:
    repo = PresetRepo(db)
    await repo.seed_defaults_if_empty()
    renamed = Preset(
        name="Films", type=TypeHint.MOVIE, format="{ny}", options=PresetOptions(trailer=True)
    )
    await repo.save(renamed, old_name="Movie (Flat)")
    names = [p.name for p in await repo.list()]
    assert names == ["Auto", "Movie (Folder)", "Films", "Series (Folder)", "Series (Flat)"]
    assert (await repo.list())[2].options.trailer


async def test_cannot_delete_last_preset(db: Database) -> None:
    repo = PresetRepo(db)
    await repo.replace_all([Preset(name="Only", type=TypeHint.AUTO, format="{ny}")])
    with pytest.raises(LastPresetError):
        await repo.delete("Only")


async def test_delete_and_replace_all(db: Database) -> None:
    repo = PresetRepo(db)
    await repo.seed_defaults_if_empty()
    await repo.delete("Auto")
    assert "Auto" not in [p.name for p in await repo.list()]
    await repo.replace_all([Preset(name="X", type=TypeHint.AUTO, format="{t}")])
    assert [p.name for p in await repo.list()] == ["X"]


async def test_match_upsert_and_none_year(db: Database) -> None:
    repo = MatchRepo(db)
    no_year = RememberedMatch(
        kind=MediaKind.EPISODE, norm_title="the office", year=None, tmdb_id=2316, label="US"
    )
    with_year = RememberedMatch(
        kind=MediaKind.EPISODE, norm_title="the office", year=2005, tmdb_id=2316, label="US"
    )
    await repo.remember(no_year)
    await repo.remember(with_year)
    await repo.remember(no_year.model_copy(update={"tmdb_id": None, "label": "skip"}))
    got = await repo.get(MediaKind.EPISODE, "the office", None)
    assert got is not None and got.tmdb_id is None and got.year is None
    assert await repo.get(MediaKind.EPISODE, "the office", 2005) == with_year
    assert await repo.get(MediaKind.MOVIE, "the office", 2005) is None
    assert len(await repo.list()) == 2


async def test_match_forget_and_clear(db: Database) -> None:
    repo = MatchRepo(db)
    m = RememberedMatch(kind=MediaKind.MOVIE, norm_title="dune", year=2021, tmdb_id=1, label="x")
    await repo.remember(m)
    await repo.forget(MediaKind.MOVIE, "dune", 2021)
    assert await repo.get(MediaKind.MOVIE, "dune", 2021) is None
    await repo.remember(m)
    await repo.clear()
    assert await repo.list() == []


async def test_cache_expiry(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = CacheRepo(db)
    now = [1000.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: now[0])
    await repo.put_json("k", {"a": [1, 2]})
    assert await repo.get_json("k", max_age_s=60) == {"a": [1, 2]}
    now[0] = 1061.0
    assert await repo.get_json("k", max_age_s=60) is None
    assert await repo.purge(older_than_s=60) == 1
    assert await repo.get_json("k", max_age_s=10_000) is None
