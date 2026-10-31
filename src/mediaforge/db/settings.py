"""App-wide settings, stored as one JSON document."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from mediaforge.db.database import Database

_KEY = "app"


class AppSettings(BaseModel):
    tmdb_key: str = ""
    tmdb_language: str = "en-US"
    theme: Literal["Dark", "Light", "System"] = "Dark"
    scan_subfolders: bool = True
    window_geometry: str | None = None
    last_preset: str | None = None
    legacy_imported: bool = False
    context_menu_presets: list[str] = []
    trailer_tools_ok: bool = False
    ytdlp_checked_at: float = 0.0
    first_start_done: bool = False
    auto_update: bool = True
    accent: str = "slate"  # AirGrabber's accent colours (ui/palettes.py); takes a restart
    # Renaming on its own (1.3): every folder is chosen in Settings › Library, nothing assumed.
    watch_folders: list[str] = []
    series_folder: str = ""
    movies_folder: str = ""
    series_preset: str = "Series (Folder)"
    movies_preset: str = "Movie (Folder)"
    automatic: Literal["off", "trial", "on"] = "off"
    clean_downloads: bool = True
    notify_needs: bool = True
    daily_summary: bool = True
    summary_sent_on: str = ""  # ISO date of the last evening summary
    tray_notice_shown: bool = False


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def load(self) -> AppSettings:
        async with self._db.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_KEY,)
        ) as cur:
            row = await cur.fetchone()
        return AppSettings.model_validate_json(row[0]) if row else AppSettings()

    async def save(self, settings: AppSettings) -> None:
        await self._db.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_KEY, settings.model_dump_json()),
        )
        await self._db.conn.commit()
