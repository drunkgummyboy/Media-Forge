"""Settings saved by an older MediaForge still load, the 1.3 library settings at their defaults."""

from mediaforge.db.database import Database
from mediaforge.db.settings import SettingsRepo


async def test_new_settings_have_defaults_and_old_settings_still_load(db: Database) -> None:
    await db.conn.execute(
        "INSERT INTO settings (key, value) VALUES ('app', ?)",
        ('{"tmdb_language": "nl-NL", "theme": "Light", "accent": "teal"}',),
    )
    await db.conn.commit()
    loaded = await SettingsRepo(db).load()
    assert (loaded.tmdb_language, loaded.theme, loaded.accent) == ("nl-NL", "Light", "teal")
    assert loaded.watch_folders == []
    assert (loaded.series_folder, loaded.movies_folder) == ("", "")
    assert (loaded.series_preset, loaded.movies_preset) == ("Series (Folder)", "Movie (Folder)")
    assert loaded.automatic == "off"
    assert loaded.clean_downloads and loaded.notify_needs and loaded.daily_summary
    assert loaded.summary_sent_on == "" and not loaded.tray_notice_shown
