import errno
import os
from pathlib import Path

import pytest

from mediaforge.core import fsops
from mediaforge.core.fsops import (
    long_path,
    make_dirs,
    move_file,
    remove_empty_dir,
    remove_file,
    stamp,
    write_new_file,
)


def test_move_file_moves(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").write_bytes(b"v")
    (tmp_path / "sub").mkdir()
    move_file(tmp_path / "a.mkv", tmp_path / "sub" / "b.mkv")
    assert (tmp_path / "sub" / "b.mkv").read_bytes() == b"v"
    assert not (tmp_path / "a.mkv").exists()


def test_move_refuses_overwrite(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").write_bytes(b"a")
    (tmp_path / "b.mkv").write_bytes(b"b")
    with pytest.raises(FileExistsError):
        move_file(tmp_path / "a.mkv", tmp_path / "b.mkv")
    assert (tmp_path / "a.mkv").read_bytes() == b"a"
    assert (tmp_path / "b.mkv").read_bytes() == b"b"


def test_case_only_rename_works(tmp_path: Path) -> None:
    # Review focus 1
    (tmp_path / "the office.mkv").write_bytes(b"v")
    move_file(tmp_path / "the office.mkv", tmp_path / "The Office.mkv")
    assert os.listdir(tmp_path) == ["The Office.mkv"]


def test_cross_volume_move_falls_back_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"data" * 1000)

    def no_rename(a: str, b: str) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(fsops.os, "rename", no_rename)
    move_file(src, dst)
    assert dst.read_bytes() == b"data" * 1000
    assert not src.exists()


def test_cross_volume_copy_failure_leaves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"data")

    def no_rename(a: str, b: str) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def broken_copy(a: object, b: object, *args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsops.os, "rename", no_rename)
    monkeypatch.setattr(fsops.shutil, "copyfileobj", broken_copy)
    with pytest.raises(OSError):
        move_file(src, dst)
    assert src.read_bytes() == b"data"
    assert not dst.exists()


def test_long_path_roundtrip(tmp_path: Path) -> None:
    # Review focus 4: deep series folders + long names go past 260 characters.
    deep = tmp_path.joinpath(*["Some Very Long Series Name (2026) - Season 01" for _ in range(6)])
    assert len(str(deep / "x.mkv")) > 300
    created = make_dirs(deep)
    assert created[-1] == deep
    size, _ = write_new_file(deep / "x.mkv", b"12345")
    assert size == 5
    move_file(deep / "x.mkv", deep / "Some Very Long Series Name (2026) - S01E01 - Pilot.mkv")
    assert os.path.exists(
        long_path(deep / "Some Very Long Series Name (2026) - S01E01 - Pilot.mkv")
    )
    assert stamp(deep / "Some Very Long Series Name (2026) - S01E01 - Pilot.mkv")[0] == 5


def test_long_path_prefixes() -> None:
    assert long_path(Path(r"\\nas\share\a")) == r"\\?\UNC\nas\share\a"
    assert long_path(Path(r"C:\a\b")) == r"\\?\C:\a\b"
    assert long_path(Path(r"\\?\C:\a")) == r"\\?\C:\a"


def test_make_dirs_reports_created_only(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert make_dirs(tmp_path / "a" / "b" / "c") == [
        tmp_path / "a" / "b",
        tmp_path / "a" / "b" / "c",
    ]
    assert make_dirs(tmp_path / "a" / "b" / "c") == []


def test_write_new_file_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "p.jpg").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        write_new_file(tmp_path / "p.jpg", b"new")
    assert (tmp_path / "p.jpg").read_bytes() == b"old"


def test_remove_helpers(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"x")
    assert remove_empty_dir(tmp_path / "d") is False
    remove_file(tmp_path / "d" / "f")
    assert remove_empty_dir(tmp_path / "d") is True
    assert not (tmp_path / "d").exists()


def test_cross_volume_never_deletes_a_file_that_appeared_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review I6: something else created dst between the existence check and the copy.
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"mine")

    def rename_then_race(a: str, b: str) -> None:
        dst.write_bytes(b"someone else's")
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(fsops.os, "rename", rename_then_race)
    with pytest.raises(FileExistsError):
        move_file(src, dst)
    assert dst.read_bytes() == b"someone else's"
    assert src.read_bytes() == b"mine"


def test_cross_volume_locked_source_leaves_no_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review I6: the copy succeeded but the source is locked; don't leave a duplicate.
    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"video")

    def no_rename(a: str, b: str) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    real_remove = os.remove

    def locked_remove(path: str) -> None:
        if path.endswith("a.mkv"):
            raise PermissionError(32, "The process cannot access the file")
        real_remove(path)

    monkeypatch.setattr(fsops.os, "rename", no_rename)
    monkeypatch.setattr(fsops.os, "remove", locked_remove)
    with pytest.raises(PermissionError):
        move_file(src, dst)
    assert src.read_bytes() == b"video"
    assert not dst.exists()
