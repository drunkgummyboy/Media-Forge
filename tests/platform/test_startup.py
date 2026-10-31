"""Start with Windows: a shortcut in the Startup folder that opens MediaForge in the tray."""

from pathlib import Path, PureWindowsPath

import pytest

from mediaforge.platform.identity import APP_USER_MODEL_ID
from mediaforge.platform.startup import (
    SHORTCUT_NAME,
    ShortcutSpec,
    StartupError,
    is_enabled,
    set_enabled,
    shortcut_spec,
)

INSTALL = Path(r"C:\Users\Someone\AppData\Local\Programs\MediaForge")
PYTHON = INSTALL / ".venv" / "Scripts" / "python.exe"


def test_startup_shortcut_spec() -> None:
    spec = shortcut_spec(INSTALL, PYTHON, Path(r"C:\Startup"))
    assert spec.path == r"C:\Startup\MediaForge.lnk" and SHORTCUT_NAME == "MediaForge.lnk"
    assert spec.target == str(PureWindowsPath(PYTHON).with_name("pythonw.exe"))
    assert spec.arguments == f'"{INSTALL}\\main.py" --tray'
    assert spec.working_dir == str(INSTALL)
    assert spec.icon == f"{INSTALL}\\src\\mediaforge\\assets\\MediaForge.ico,0"
    assert spec.app_id == APP_USER_MODEL_ID


async def test_startup_enable_and_disable(tmp_path: Path) -> None:
    install = tmp_path / "Programs" / "MediaForge"
    install.mkdir(parents=True)
    startup = tmp_path / "Startup"
    written: list[ShortcutSpec] = []

    async def write(spec: ShortcutSpec) -> None:  # stands in for PowerShell
        written.append(spec)
        Path(spec.path).write_bytes(b"lnk")

    assert not is_enabled(startup_dir=startup)
    await set_enabled(True, startup_dir=startup, install_dir=install, python=PYTHON, write=write)
    assert is_enabled(startup_dir=startup)
    assert written[0].arguments.endswith("--tray")
    await set_enabled(False, startup_dir=startup, install_dir=install, python=PYTHON, write=write)
    assert not is_enabled(startup_dir=startup)


async def test_development_copies_dont_start_with_windows(tmp_path: Path) -> None:
    source = tmp_path / "MediaForge"
    (source / "tests").mkdir(parents=True)

    async def write(spec: ShortcutSpec) -> None:
        raise AssertionError("never written for a development copy")

    with pytest.raises(StartupError, match="Only an installed MediaForge can start with Windows."):
        await set_enabled(True, startup_dir=tmp_path / "Startup", install_dir=source, write=write)
