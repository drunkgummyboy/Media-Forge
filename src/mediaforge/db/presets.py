"""The user's presets, in the order they appear in the dropdown."""

from __future__ import annotations

from collections.abc import Sequence

from mediaforge.db.database import Database
from mediaforge.models.media import TypeHint
from mediaforge.models.preset import DEFAULT_PRESETS, Preset, PresetOptions


class LastPresetError(Exception):
    """The last remaining preset can't be deleted."""


class PresetRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[Preset]:
        async with self._db.conn.execute(
            "SELECT name, type, format, options FROM presets ORDER BY position"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Preset(
                name=name,
                type=TypeHint(type_),
                format=fmt,
                options=PresetOptions.model_validate_json(options),
            )
            for name, type_, fmt, options in rows
        ]

    async def _count(self) -> int:
        async with self._db.conn.execute("SELECT COUNT(*) FROM presets") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def save(self, preset: Preset, *, old_name: str | None = None) -> None:
        conn = self._db.conn
        values = (preset.type.value, preset.format, preset.options.model_dump_json())
        if old_name is not None and old_name != preset.name:
            await conn.execute("DELETE FROM presets WHERE name = ?", (preset.name,))
            await conn.execute(
                "UPDATE presets SET name = ?, type = ?, format = ?, options = ? WHERE name = ?",
                (preset.name, *values, old_name),
            )
        else:
            await conn.execute(
                "INSERT INTO presets (name, position, type, format, options) "
                "VALUES (?, (SELECT COALESCE(MAX(position), -1) + 1 FROM presets), ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET type = excluded.type, "
                "format = excluded.format, options = excluded.options",
                (preset.name, *values),
            )
        await conn.commit()

    async def delete(self, name: str) -> None:
        if await self._count() <= 1:
            raise LastPresetError(name)
        await self._db.conn.execute("DELETE FROM presets WHERE name = ?", (name,))
        await self._db.conn.commit()

    async def replace_all(self, presets: Sequence[Preset]) -> None:
        conn = self._db.conn
        await conn.execute("DELETE FROM presets")
        await conn.executemany(
            "INSERT INTO presets (name, position, type, format, options) VALUES (?, ?, ?, ?, ?)",
            [
                (p.name, i, p.type.value, p.format, p.options.model_dump_json())
                for i, p in enumerate(presets)
            ],
        )
        await conn.commit()

    async def seed_defaults_if_empty(self) -> None:
        if await self._count() == 0:
            await self.replace_all(DEFAULT_PRESETS)
