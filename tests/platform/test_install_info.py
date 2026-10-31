from collections.abc import Mapping
from pathlib import Path

from mediaforge.platform.install_info import (
    UNINSTALL_KEY,
    RegistryValue,
    ensure_uninstall_entry,
    entry_values,
)


class FakeRegistry:
    def __init__(self) -> None:
        self.keys: dict[str, dict[str, RegistryValue]] = {}
        self.writes: list[dict[str, RegistryValue]] = []

    def read(self, key: str) -> dict[str, RegistryValue] | None:
        values = self.keys.get(key)
        return dict(values) if values is not None else None

    def write(self, key: str, values: Mapping[str, RegistryValue]) -> None:
        self.keys.setdefault(key, {}).update(values)
        self.writes.append(dict(values))


def test_creates_entry_when_missing(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x" * 3000, encoding="utf-8")
    registry = FakeRegistry()
    ensure_uninstall_entry(tmp_path, "1.1.0", registry=registry)
    entry = registry.keys[UNINSTALL_KEY]
    assert entry["DisplayName"] == "MediaForge"
    assert entry["DisplayVersion"] == "1.1.0"
    assert entry["Publisher"] == "drunkgummyboy"
    assert entry["URLInfoAbout"] == "https://github.com/drunkgummyboy/Media-Forge"
    assert entry["InstallLocation"] == str(tmp_path)
    assert entry["DisplayIcon"] == str(
        tmp_path / "src" / "mediaforge" / "assets" / "MediaForge.ico"
    )
    assert entry["EstimatedSize"] == 3
    assert (entry["NoModify"], entry["NoRepair"]) == (1, 1)
    assert str(entry["QuietUninstallString"]).endswith(" -Quiet")


def test_refreshes_only_display_version(tmp_path: Path) -> None:
    registry = FakeRegistry()
    ensure_uninstall_entry(tmp_path, "1.1.0", registry=registry)
    ensure_uninstall_entry(tmp_path, "1.2.0", registry=registry)
    assert registry.writes[-1] == {"DisplayVersion": "1.2.0"}
    ensure_uninstall_entry(tmp_path, "1.2.0", registry=registry)
    assert len(registry.writes) == 2  # nothing to change, nothing written


def test_leaves_another_folders_entry_alone(tmp_path: Path) -> None:
    registry = FakeRegistry()
    ensure_uninstall_entry(tmp_path / "a", "1.1.0", registry=registry)
    ensure_uninstall_entry(tmp_path / "b", "1.2.0", registry=registry)
    assert registry.keys[UNINSTALL_KEY]["DisplayVersion"] == "1.1.0"
    assert len(registry.writes) == 1


def test_uninstall_string_quotes_paths_with_spaces() -> None:
    # Review focus 3: user names with spaces and non-ASCII letters.
    root = r"C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge"
    values = entry_values(root, "1.1.0")
    assert values["UninstallString"] == (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "
        rf'"{root}\src\mediaforge\uninstall.ps1"'
    )


def test_registry_errors_are_not_raised(tmp_path: Path) -> None:
    class Broken(FakeRegistry):
        def read(self, key: str) -> dict[str, RegistryValue] | None:
            raise PermissionError("denied")

    ensure_uninstall_entry(tmp_path, "1.1.0", registry=Broken())  # logs, never raises
