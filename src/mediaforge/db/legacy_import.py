"""One-time import of the old mediaforge_config.json and mediaforge_undo.json."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from mediaforge.core import fsops
from mediaforge.db.history import HistoryRepo
from mediaforge.db.presets import PresetRepo
from mediaforge.db.settings import SettingsRepo
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import TypeHint
from mediaforge.models.preset import LEGACY_OPTION_KEYS, Preset, PresetOptions

log = logging.getLogger("mediaforge.import")

CONFIG_NAME = "mediaforge_config.json"
UNDO_NAME = "mediaforge_undo.json"


class ImportReport(BaseModel):
    presets: int = 0
    key_imported: bool = False
    history_files: int = 0


def find_legacy(dirs: Sequence[Path]) -> tuple[Path | None, Path | None]:
    config = next((d / CONFIG_NAME for d in dirs if (d / CONFIG_NAME).is_file()), None)
    undo = next((d / UNDO_NAME for d in dirs if (d / UNDO_NAME).is_file()), None)
    return config, undo


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("Couldn't read %s: %s", path.name, exc)
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _preset(name: str, raw: dict[str, Any]) -> Preset:
    try:
        type_hint = TypeHint(raw.get("type", "auto"))
    except ValueError:
        type_hint = TypeHint.AUTO
    checks = cast(list[str], raw.get("chk") or [])
    options = {LEGACY_OPTION_KEYS[c]: True for c in checks if c in LEGACY_OPTION_KEYS}
    return Preset(
        name=name,
        type=type_hint,
        format=str(raw.get("format") or "{ny}"),
        options=PresetOptions(**options),
    )


def _try_stamp(path: Path) -> tuple[int, int] | None:
    try:
        return fsops.stamp(path)
    except OSError:
        return None


async def import_legacy(
    config: Path | None,
    undo: Path | None,
    settings: SettingsRepo,
    presets: PresetRepo,
    history: HistoryRepo,
    *,
    force: bool = False,
) -> ImportReport:
    """Import once on first start; force=True imports a file the user picked later."""
    s = await settings.load()
    report = ImportReport()
    if s.legacy_imported and not force:
        return report
    updates: dict[str, Any] = {"legacy_imported": True}

    if config is not None:
        data = await asyncio.to_thread(_read_json, config)
        key = str(data.get("TMDB_API_KEY") or "").strip()
        if key and not s.tmdb_key:
            updates["tmdb_key"] = key
            report.key_imported = True
        if data.get("theme") in ("Dark", "Light", "System"):
            updates["theme"] = data["theme"]
        if isinstance(data.get("window_geometry"), str):
            updates["window_geometry"] = data["window_geometry"]
        raw_presets = data.get("presets")
        if isinstance(raw_presets, dict) and raw_presets:
            items = cast(dict[str, Any], raw_presets)
            imported = [
                _preset(name, cast(dict[str, Any], raw))
                for name, raw in items.items()
                if isinstance(raw, dict)
            ]
            if imported:
                await presets.replace_all(imported)
                report.presets = len(imported)

    if undo is not None:
        data = await asyncio.to_thread(_read_json, undo)
        renames = [r for r in cast(list[Any], data.get("renames") or []) if len(r) == 2]
        created = [str(c) for c in cast(list[Any], data.get("created") or [])]
        if renames or created:
            mtime = (await asyncio.to_thread(undo.stat)).st_mtime
            when = datetime.fromtimestamp(mtime)
            run_id = await history.start_run(RunSource.IMPORT, None, started_at=when)
            for old, new in renames:
                op = await history.record_pending(run_id, OpKind.RENAME, Path(old), Path(new))
                await history.mark_done(op)
            for path in created:
                op = await history.record_pending(run_id, OpKind.CREATE_FILE, None, Path(path))
                stamp = await asyncio.to_thread(_try_stamp, Path(path))
                await history.mark_done(
                    op, size=stamp[0] if stamp else None, mtime_ns=stamp[1] if stamp else None
                )
            await history.finish_run(run_id)
            report.history_files = len(renames)

    await settings.save(s.model_copy(update=updates))
    log.info(
        "Imported %d preset(s) and %d past rename(s) from the old MediaForge",
        report.presets,
        report.history_files,
    )
    return report
