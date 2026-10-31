"""Review I7/I8: every disk check uses extended-length paths (so deep NAS folders work on
PCs without LongPathsEnabled) and none of them runs on the window's thread."""

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from mediaforge.core import cleanup, fsops, planner
from mediaforge.core import queue as queue_module
from mediaforge.core.matcher import Resolution
from mediaforge.core.queue import Queue, scan_paths
from mediaforge.models.media import ParsedMedia, ResolvedMedia
from mediaforge.models.plan import PlanInput
from mediaforge.models.preset import PresetOptions
from mediaforge.models.tmdb import TmdbResult

EXTENDED = "\\\\?\\"


def record(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> list[str]:
    seen: list[str] = []
    real = getattr(module, name)

    def spy(path: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return seen


def test_scan_walks_with_extended_paths_and_returns_normal_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.mkv").write_bytes(b"v")
    walked = record(monkeypatch, fsops.os, "walk")
    result = scan_paths([tmp_path], recursive=True)
    assert walked and all(w.startswith(EXTENDED) for w in walked)
    assert result.files == [(tmp_path / "sub" / "a.mkv", tmp_path)]
    assert result.folders == [tmp_path]


def test_planner_checks_existence_with_extended_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked = record(monkeypatch, fsops.os.path, "lexists")
    parsed = ParsedMedia(source_name="x.mkv", kind="movie", title="Dune")  # type: ignore[arg-type]
    media = ResolvedMedia.offline(parsed)
    item = PlanInput(
        item_id=1, source=tmp_path / "x.mkv", root=tmp_path, dropped_folder=None, media=media
    )
    planner.build_plan([item], "{ny}", PresetOptions())
    assert checked and all(c.startswith(EXTENDED) for c in checked)


def test_cleanup_walks_with_extended_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Thumbs.db").write_bytes(b"j")
    walked = record(monkeypatch, fsops.os, "walk")
    assert cleanup.find_cleanup_targets(tmp_path) == [tmp_path / "Thumbs.db"]
    assert walked and all(w.startswith(EXTENDED) for w in walked)


class _Resolver:
    async def resolve(self, parsed: ParsedMedia) -> Resolution:
        return Resolution(media=ResolvedMedia.offline(parsed))

    async def apply_choice(
        self, parsed: ParsedMedia, chosen: TmdbResult | None, *, remember: bool = True
    ) -> ResolvedMedia:
        return ResolvedMedia.offline(parsed)


async def test_folder_checks_never_run_on_the_loop_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checking a dropped path on an unreachable share can block for the SMB timeout.
    (tmp_path / "a.mkv").write_bytes(b"v")
    threads: list[str] = []
    real = fsops.is_dir

    def spy(path: Path) -> bool:
        threads.append(threading.current_thread().name)
        return real(path)

    monkeypatch.setattr(queue_module.fsops, "is_dir", spy)
    q = Queue(_Resolver(), lambda e: None)
    await q.add_paths([tmp_path, tmp_path / "a.mkv"], recursive=True)
    assert threads and "MainThread" not in threads
    assert os.path.normcase(str(q.items[0].path)) == os.path.normcase(str(tmp_path / "a.mkv"))
