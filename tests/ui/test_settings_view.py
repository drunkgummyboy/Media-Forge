import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from mediaforge.api.tmdb import TmdbClient
from mediaforge.app import Services
from mediaforge.db.matches import RememberedMatch
from mediaforge.models.media import MediaKind, TypeHint
from mediaforge.models.preset import Preset, PresetOptions
from mediaforge.platform import context_menu
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.first_start import FirstStart
from mediaforge.ui.settings_view import SettingsView
from mediaforge.ui.theme import DARK

from helpers.tk import pump


@pytest_asyncio.fixture
async def view(tk_root: DnDCTk, services: Services) -> AsyncIterator[SettingsView]:
    v = SettingsView(tk_root, services, DARK)
    v.pack(fill="both", expand=True)
    told: list[str] = []
    changed: list[int] = []

    async def note(title: str, message: str) -> None:
        told.append(message)

    v.inform = note
    v.on_presets_changed = lambda: changed.append(1)
    v.told, v.changed = told, changed  # type: ignore[attr-defined]
    await v.reload()
    await pump(tk_root, 0.05)
    try:
        yield v
    finally:
        v.destroy()


async def test_save_preset_updates_queue_dropdown(view: SettingsView, services: Services) -> None:
    mine = Preset(
        name="Mine",
        type=TypeHint.TV,
        format="{t} {s00e00}",
        options=PresetOptions(season_poster=True),
    )
    await view.save_preset(mine, old_name=None)
    assert "Mine" in [p.name for p in await services.presets.list()]
    assert view.changed  # type: ignore[attr-defined]


async def test_rename_preset(view: SettingsView, services: Services) -> None:
    renamed = Preset(name="Films", type=TypeHint.MOVIE, format="{ny}")
    await view.save_preset(renamed, old_name="Movie (Flat)")
    names = [p.name for p in await services.presets.list()]
    assert "Films" in names and "Movie (Flat)" not in names


async def test_delete_last_preset_shows_error(view: SettingsView, services: Services) -> None:
    await services.presets.replace_all([Preset(name="Only", type=TypeHint.AUTO, format="{ny}")])
    await view.reload()
    await view.delete_preset("Only")
    assert [p.name for p in await services.presets.list()] == ["Only"]
    assert view.told and "last preset" in view.told[-1]  # type: ignore[attr-defined]


async def test_format_sample_shows_rendered_name(tk_root: DnDCTk, view: SettingsView) -> None:
    view.editor_format.set("{ny} - {s00e00} - {en}")
    await pump(tk_root, 0.05)
    assert "S01E02 - Theseus" in view.format_sample.cget("text")


async def test_key_check_uses_validate(view: SettingsView, monkeypatch: pytest.MonkeyPatch) -> None:
    async def accept(self: TmdbClient) -> bool:
        return True

    async def reject(self: TmdbClient) -> bool:
        return False

    monkeypatch.setattr(TmdbClient, "validate_key", accept)
    assert await view.check_key("good-key-123") is True
    assert "works" in view.key_status.cget("text")
    monkeypatch.setattr(TmdbClient, "validate_key", reject)
    assert await view.check_key("bad-key-123") is False
    assert "rejected" in view.key_status.cget("text")


async def test_save_tmdb_rebuilds_client(view: SettingsView, services: Services) -> None:
    assert services.tmdb is None
    await view.save_tmdb("abcdef123456", "nl-NL")
    settings = await services.settings.load()
    assert (settings.tmdb_key, settings.tmdb_language) == ("abcdef123456", "nl-NL")
    assert services.tmdb is not None and services.tmdb.language == "nl-NL"


async def test_context_menu_buttons_call_install_with_entries(
    view: SettingsView, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed: list[Any] = []
    monkeypatch.setattr(context_menu, "install", lambda entries: installed.extend(entries))
    monkeypatch.setattr(context_menu, "installed_command", lambda: None)
    monkeypatch.setattr(context_menu, "legacy_installed", lambda: False)
    await view.install_menu(["Auto", "Series (Flat)"])
    verbs = [e.values.get("MUIVerb") for e in installed]
    assert "Forge: Auto" in verbs and "Forge: Series (Flat)" in verbs
    assert (await services.settings.load()).context_menu_presets == ["Auto", "Series (Flat)"]


async def test_stale_menu_detected(view: SettingsView, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_menu,
        "installed_command",
        lambda: '"D:\\old\\MediaForge.exe" --confirm --preset "Auto" "%1"',
    )
    monkeypatch.setattr(context_menu, "legacy_installed", lambda: True)
    status = view.menu_status()
    assert "old location" in status and "Old MediaForge menu" in status


async def test_matches_can_be_forgotten(view: SettingsView, services: Services) -> None:
    m = RememberedMatch(
        kind=MediaKind.EPISODE,
        norm_title="the office",
        year=None,
        tmdb_id=1,
        label="The Office (2005)",
    )
    await services.matches.remember(m)
    await view.reload()
    await view.forget_match(m)
    assert await services.matches.list() == []


async def test_theme_saved(view: SettingsView, services: Services) -> None:
    await view.set_theme("Light")
    assert (await services.settings.load()).theme == "Light"


async def test_first_start_imports_legacy_and_marks_done(
    tk_root: DnDCTk, services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old"
    old.mkdir()
    config = {
        "TMDB_API_KEY": "legacy-key-1234",
        "presets": {"Legacy Only": {"type": "tv", "format": "{t}", "chk": ["chk_trailer"]}},
    }
    (old / "mediaforge_config.json").write_text(json.dumps(config), encoding="utf-8")
    first = FirstStart(tk_root, services, DARK)
    await first.import_from(old / "mediaforge_config.json")
    await first.finish()
    assert [p.name for p in await services.presets.list()] == ["Legacy Only"]
    settings = await services.settings.load()
    assert settings.first_start_done and settings.tmdb_key == "legacy-key-1234"
    assert services.tmdb is not None
    assert not first.winfo_exists()


async def test_edited_preset_is_highlighted(view: SettingsView) -> None:
    buttons = view.preset_list.winfo_children()
    assert view.editing == "Auto"
    assert buttons[0].cget("text_color") == DARK.link  # type: ignore[union-attr]


async def test_settings_shows_version_and_offers_update(
    view: SettingsView, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    fake = FakeUpdates("1.2.0")
    services.updates = fake  # type: ignore[assignment]
    restarts: list[int] = []
    view.on_restart = lambda: restarts.append(1)
    view.show_version()
    assert "1.1.0" in view.version_label.cget("text")
    await view.check_for_updates()
    assert view.update_button.cget("text") == "Update to 1.2.0"
    await view.update_now()
    assert fake.installed == ["1.2.0"]
    assert restarts == [1]


async def test_settings_up_to_date_and_development_copy(
    view: SettingsView, services: Services
) -> None:
    from helpers.fake_updates import FakeUpdates

    services.updates = FakeUpdates("1.1.0")  # type: ignore[assignment]
    await view.check_for_updates()
    assert "up to date" in view.update_label.cget("text")
    assert view.update_button.cget("text") == "Check for updates"
    services.updates = FakeUpdates("1.2.0", installable=False)  # type: ignore[assignment]
    await view.check_for_updates()
    assert "development copy" in view.update_label.cget("text")
    assert view.update_button.cget("text") == "Check for updates"


async def test_automatic_updates_switch_is_saved(view: SettingsView, services: Services) -> None:
    assert services.app_settings.auto_update is True
    await view.set_auto_update(False)
    assert (await services.settings.load()).auto_update is False
    assert services.app_settings.auto_update is False


async def test_all_ticked_saves_empty_list(
    view: SettingsView, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Empty means "every preset", so presets added later appear in the menu too.
    monkeypatch.setattr(context_menu, "install", lambda entries: None)
    monkeypatch.setattr(context_menu, "installed_command", lambda: None)
    monkeypatch.setattr(context_menu, "legacy_installed", lambda: False)
    await view.install_menu([p.name for p in await services.presets.list()])
    assert (await services.settings.load()).context_menu_presets == []


async def test_settings_has_its_tabs(view: SettingsView) -> None:
    assert view.tab_names == ["Naming", "TMDB", "Right-click menu", "Looks", "About"]
    assert view.tabs.get() == "Naming"
    # Each section lives in its tab, under the same names as before.
    assert view.theme.winfo_toplevel() is view.winfo_toplevel()


async def test_accent_is_saved_and_used_next_start(view: SettingsView, services: Services) -> None:
    from mediaforge.app import start_palette

    assert services.app_settings.accent == "slate"
    assert set(view.accent_buttons) >= {"slate", "teal", "graphite"}
    await view.set_accent("teal")
    saved = await services.settings.load()
    assert saved.accent == "teal" and services.app_settings.accent == "teal"
    assert start_palette(saved.model_copy(update={"theme": "Dark"})).accent == "#2E7A76"
    assert view.accent_buttons["teal"].cget("text") == "✓"
    assert view.accent_buttons["slate"].cget("text") == ""
