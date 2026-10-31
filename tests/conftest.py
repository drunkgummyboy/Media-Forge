from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mediaforge_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from the real %APPDATA% / %LOCALAPPDATA%."""
    home = tmp_path / "mfhome"
    monkeypatch.setenv("MEDIAFORGE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_real_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real right-click menu: as far as tests know it's off,
    and writing it fails loudly unless a test puts its own fake in place."""
    from mediaforge.platform import context_menu

    def refuse(*args: object) -> None:
        raise AssertionError("a test tried to change the real right-click menu")

    monkeypatch.setattr(context_menu, "installed_command", lambda: None)
    monkeypatch.setattr(context_menu, "install", refuse)
    monkeypatch.setattr(context_menu, "uninstall", refuse)
