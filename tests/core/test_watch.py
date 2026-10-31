"""A download counts as finished only when it has stopped changing and nothing holds it open."""

import os
from pathlib import Path

import pytest

from mediaforge.core.watch import QUIET_S, Watcher, candidates, is_sample


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def video(path: Path, size: int = 10, age_s: float = 3600, clock: Clock | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    when = (clock.now if clock else 0) - age_s
    os.utime(path, (when, when))
    return path


def watcher(clock: Clock, locked: set[Path] | None = None) -> Watcher:
    return Watcher(now=clock, can_open=lambda p: p not in (locked or set()))


def test_a_new_file_waits_for_a_second_look(tmp_path: Path) -> None:
    clock = Clock(1_000_000)
    file = video(tmp_path / "ep.mkv", clock=clock)
    w = watcher(clock)
    assert w.finished([file]) == []  # first seen: not yet
    clock.now += 300
    assert w.finished([file]) == [file]


def test_a_growing_file_is_not_finished(tmp_path: Path) -> None:
    clock = Clock(1_000_000)
    file = video(tmp_path / "ep.mkv", size=10, clock=clock)
    w = watcher(clock)
    w.finished([file])
    clock.now += 300
    video(file, size=20, clock=clock)  # more arrived since the last look
    assert w.finished([file]) == []
    clock.now += 300
    assert w.finished([file]) == [file]


def test_a_file_touched_recently_is_not_finished(tmp_path: Path) -> None:
    clock = Clock(1_000_000)
    file = video(tmp_path / "ep.mkv", age_s=300, clock=clock)  # written 5 minutes ago
    w = watcher(clock)
    w.finished([file])
    assert w.finished([file]) == []  # the same twice, but not quiet for 10 minutes
    clock.now += QUIET_S
    assert w.finished([file]) == [file]


def test_a_locked_file_is_not_finished(tmp_path: Path) -> None:
    clock = Clock(1_000_000)
    file = video(tmp_path / "ep.mkv", clock=clock)
    locked = {file}
    w = watcher(clock, locked)
    w.finished([file])
    clock.now += 300
    assert w.finished([file]) == []
    locked.clear()
    assert w.finished([file]) == [file]


def test_temporary_and_sample_files_are_never_candidates(tmp_path: Path) -> None:
    for name in (
        "ep.mkv.part",
        "ep2.!qB",
        "Show.S01E01/Sample/show-sample.mkv",
        "Show.S01E01/sample.mkv",
        "@eaDir/x.mkv",
        "#recycle/x.mkv",
        ".hidden/x.mkv",
        "~x.mkv",
        ".x.mkv",
        "notes.txt",
    ):
        video(tmp_path / name)
    video(tmp_path / "busy.mkv")
    video(tmp_path / "busy.mkv.aria2")  # the downloader is still writing busy.mkv
    keep = video(tmp_path / "Show.S01E01" / "show.s01e01.mkv")
    assert candidates(tmp_path) == [keep]
    assert is_sample(Path("Show.S01E01-SAMPLE.mkv")) and not is_sample(Path("Samples.Of.Life.mkv"))


def test_files_in_subfolders_count(tmp_path: Path) -> None:
    a = video(tmp_path / "A" / "B" / "movie.2020.mkv")
    b = video(tmp_path / "top.mp4")
    assert sorted(candidates(tmp_path)) == sorted([a, b])


def test_a_vanished_file_is_forgotten(tmp_path: Path) -> None:
    clock = Clock(1_000_000)
    file = video(tmp_path / "ep.mkv", clock=clock)
    w = watcher(clock)
    w.finished([file])
    file.unlink()
    clock.now += 300
    assert w.finished([file]) == []  # listed, then gone before its size was read: skipped
    assert w.finished([]) == []
    video(file, clock=clock)
    clock.now += 300
    assert w.finished([file]) == []  # back again: starts over, waits for a second look


def test_an_unreadable_root_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        candidates(tmp_path / "not-there")


def test_exclusive_open_sees_a_file_in_use(tmp_path: Path) -> None:
    from mediaforge.core.watch import exclusive_open

    file = video(tmp_path / "ep.mkv")
    assert exclusive_open(file)
    with open(file, "rb"):
        assert not exclusive_open(file)  # a downloader (or anything) still has it open
    assert not exclusive_open(tmp_path / "gone.mkv")
