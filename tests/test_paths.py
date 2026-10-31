from pathlib import Path

from mediaforge.platform.paths import app_paths


def test_home_override_puts_everything_under_home(tmp_path: Path) -> None:
    p = app_paths({"MEDIAFORGE_HOME": str(tmp_path)})
    assert p.db_file == tmp_path / "mediaforge.db"
    assert p.logs_dir == tmp_path / "logs"
    assert p.tools_dir == tmp_path / "tools"
    assert p.images_dir == tmp_path / "cache" / "images"
    assert p.instance_key_file == tmp_path / "instance.key"


def test_default_uses_appdata_and_localappdata() -> None:
    p = app_paths({"APPDATA": r"C:\R", "LOCALAPPDATA": r"C:\L"})
    assert p.db_file == Path(r"C:\R\MediaForge\mediaforge.db")
    assert p.logs_dir == Path(r"C:\L\MediaForge\logs")
    assert p.cache_dir == Path(r"C:\L\MediaForge\cache")


def test_ensure_creates_dirs(tmp_path: Path) -> None:
    p = app_paths({"MEDIAFORGE_HOME": str(tmp_path / "x")})
    p.ensure()
    assert p.data_dir.is_dir()
    assert p.logs_dir.is_dir()
    assert p.images_dir.is_dir()
    assert p.tools_dir.is_dir()
