"""The Explorer right-click menu: a "MediaForge" submenu with one entry per preset.

Written under HKEY_CURRENT_USER, so it needs no administrator rights.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BASES = (
    r"Software\Classes\*\shell\MediaForge",
    r"Software\Classes\Directory\shell\MediaForge",
)
LEGACY_HKLM_KEYS = (
    r"SOFTWARE\Classes\*\shell\MediaForgeCommands",
    r"SOFTWARE\Classes\Folder\shell\MediaForgeCommands",
)
_LEGACY_COMMAND_STORE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell"


@dataclass(frozen=True)
class RegEntry:
    key: str
    values: dict[str, str]  # "" is the key's default value


def command_prefix(launcher: Path | None = None) -> list[str]:
    """How Explorer should start MediaForge: this Python's pythonw.exe with main.py (installed
    copies; `launch.LAUNCHER`), or with `-m mediaforge` when started that way."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else exe)
    if launcher is not None:
        return [runner, str(launcher)]
    return [runner, "-m", "mediaforge"]


def default_icon() -> str:
    """The icon file that ships inside the package."""
    return str(Path(__file__).resolve().parents[1] / "assets" / "MediaForge.ico")


def menu_presets(all_names: Sequence[str], chosen: Sequence[str]) -> list[str]:
    """The presets the menu shows: the chosen ones that still exist; none chosen (or none of
    them left) means every preset, so presets added later appear too."""
    kept = [name for name in all_names if name in chosen]
    return kept or list(all_names)


def _quote(part: str) -> str:
    return '"' + part.replace('"', "") + '"'


def build_entries(presets: Sequence[str], prefix: Sequence[str], icon: str) -> list[RegEntry]:
    launcher = " ".join(_quote(p) for p in prefix)
    entries: list[RegEntry] = []
    for base in BASES:
        entries.append(RegEntry(base, {"MUIVerb": "MediaForge", "SubCommands": "", "Icon": icon}))
        for index, preset in enumerate(presets, start=1):
            safe = re.sub(r"[^A-Za-z0-9]", "", preset) or "Preset"
            item = rf"{base}\shell\{index:02d}_{safe}"
            entries.append(
                RegEntry(item, {"MUIVerb": f"Forge: {preset}", "MultiSelectModel": "Player"})
            )
            command = f'{launcher} --confirm --preset {_quote(preset)} "%1"'
            entries.append(RegEntry(rf"{item}\command", {"": command}))
    return entries


def _reg_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_reg_file(entries: Sequence[RegEntry], hive: str = "HKEY_CURRENT_USER") -> str:
    lines = ["Windows Registry Editor Version 5.00", ""]
    for entry in entries:
        lines.append(f"[{hive}\\{entry.key}]")
        for name, value in entry.values.items():
            label = "@" if name == "" else _reg_string(name)
            lines.append(f"{label}={_reg_string(value)}")
        lines.append("")
    return "\r\n".join(lines) + "\r\n"


# --- registry access (Windows only; never called by tests) ------------------------------


def _delete_tree(root: int, key: str) -> None:
    import winreg

    try:
        handle = winreg.OpenKey(root, key, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    with handle:
        while True:
            try:
                child = winreg.EnumKey(handle, 0)
            except OSError:
                break
            _delete_tree(root, rf"{key}\{child}")
    winreg.DeleteKey(root, key)


def uninstall() -> None:
    import winreg

    for base in BASES:
        _delete_tree(winreg.HKEY_CURRENT_USER, base)


def install(entries: Sequence[RegEntry]) -> None:
    import winreg

    uninstall()
    for entry in entries:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, entry.key) as handle:
            for name, value in entry.values.items():
                winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def refresh(all_names: Sequence[str], chosen: Sequence[str], launcher: Path | None) -> bool:
    """Rewrite the menu with the current presets, if the menu is on and already starts this
    copy (`launcher`, main.py). A menu that starts another copy (the installed one, while a
    development copy runs) is never taken over. Blocking."""
    current = installed_command()
    if current is None or launcher is None:
        return False
    if f'"{launcher}"'.casefold() not in current.casefold():
        return False
    names = menu_presets(all_names, chosen)
    if not names:
        return False
    install(build_entries(names, command_prefix(launcher), default_icon()))
    return True


def installed_command() -> str | None:
    """The first menu entry's command line, to spot a menu pointing at an old location."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{BASES[0]}\shell") as shell:
            first = winreg.EnumKey(shell, 0)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{BASES[0]}\shell\{first}\command") as k:
            value, _ = winreg.QueryValueEx(k, "")
    except OSError:
        return None
    return str(value)


def legacy_installed() -> bool:
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LEGACY_HKLM_KEYS[0]).Close()
    except OSError:
        return False
    return True


def _legacy_subcommands() -> list[str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LEGACY_HKLM_KEYS[0]) as k:
            value, _ = winreg.QueryValueEx(k, "SubCommands")
    except OSError:
        return []
    return [name for name in str(value).split(";") if name]


def legacy_removal_reg() -> str:
    """A .reg file that removes the old (HKLM) menu. Needs an administrator to run it."""
    hive = "HKEY_LOCAL_MACHINE"
    lines = ["Windows Registry Editor Version 5.00", ""]
    lines += [f"[-{hive}\\{key}]" for key in LEGACY_HKLM_KEYS]
    lines += [f"[-{hive}\\{_LEGACY_COMMAND_STORE}\\{name}]" for name in _legacy_subcommands()]
    return "\r\n".join(lines) + "\r\n"
