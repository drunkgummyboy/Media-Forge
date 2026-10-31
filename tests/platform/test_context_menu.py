from pathlib import Path

import pytest

from mediaforge.platform import context_menu
from mediaforge.platform.context_menu import (
    BASES,
    RegEntry,
    build_entries,
    legacy_removal_reg,
    to_reg_file,
)

EXE = r"C:\Program Files\MediaForge\MediaForge.exe"


def test_build_entries_structure() -> None:
    entries = build_entries(["Auto", "Series (Folder)"], [EXE], EXE)
    keys = [e.key for e in entries]
    base = BASES[0]
    assert keys[:5] == [
        base,
        rf"{base}\shell\01_Auto",
        rf"{base}\shell\01_Auto\command",
        rf"{base}\shell\02_SeriesFolder",
        rf"{base}\shell\02_SeriesFolder\command",
    ]
    assert len(entries) == 10  # same five keys for folders
    by_key = {e.key: e.values for e in entries}
    assert by_key[base] == {"MUIVerb": "MediaForge", "SubCommands": "", "Icon": EXE}
    assert by_key[rf"{base}\shell\02_SeriesFolder"] == {
        "MUIVerb": "Forge: Series (Folder)",
        "MultiSelectModel": "Player",
    }
    assert by_key[rf"{base}\shell\02_SeriesFolder\command"] == {
        "": f'"{EXE}" --confirm --preset "Series (Folder)" "%1"'
    }
    assert BASES == (
        r"Software\Classes\*\shell\MediaForge",
        r"Software\Classes\Directory\shell\MediaForge",
    )


def test_source_mode_command_prefix() -> None:
    entries = build_entries(["Auto"], [r"C:\Py\pythonw.exe", "-m", "mediaforge"], "x.ico")
    assert entries[2].values[""] == (
        r'"C:\Py\pythonw.exe" "-m" "mediaforge" --confirm --preset "Auto" "%1"'
    )


def test_reg_file_escaping() -> None:
    text = to_reg_file([RegEntry(r"Software\X", {"": r'"C:\a b\c.exe" "%1"', "Name": "v"})])
    assert text.startswith("Windows Registry Editor Version 5.00\r\n")
    assert r"[HKEY_CURRENT_USER\Software\X]" in text
    assert r'@="\"C:\\a b\\c.exe\" \"%1\""' in text
    assert '"Name"="v"' in text


def test_legacy_removal_reg_uses_minus_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_menu, "_legacy_subcommands", lambda: ["MediaForge_Auto"])
    text = legacy_removal_reg()
    assert r"[-HKEY_LOCAL_MACHINE\SOFTWARE\Classes\*\shell\MediaForgeCommands]" in text
    assert r"[-HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Folder\shell\MediaForgeCommands]" in text
    assert (
        r"[-HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"
        r"\CommandStore\shell\MediaForge_Auto]" in text
    )


def test_command_prefix_from_source_uses_module() -> None:
    prefix = context_menu.command_prefix()
    assert prefix[-2:] == ["-m", "mediaforge"]


def test_command_prefix_uses_the_launcher(tmp_path: object) -> None:
    from pathlib import Path

    launcher = Path(r"C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge\main.py")
    prefix = context_menu.command_prefix(launcher)
    assert prefix[-1] == str(launcher)
    assert prefix[0].lower().endswith(("pythonw.exe", "python.exe"))
    assert len(prefix) == 2


def test_menu_command_quotes_non_ascii_paths() -> None:
    # Review focus 3: user names with spaces and non-ASCII letters.
    pyw = r"C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge\.venv\Scripts\pythonw.exe"
    main = r"C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge\main.py"
    entries = build_entries(["Auto"], [pyw, main], "x.ico")
    assert entries[2].values[""] == f'"{pyw}" "{main}" --confirm --preset "Auto" "%1"'


def test_menu_presets_empty_means_all_and_drops_deleted() -> None:
    names = ["Auto", "Movie (Flat)", "Series (Folder)"]
    assert context_menu.menu_presets(names, []) == names
    assert context_menu.menu_presets(names, ["Series (Folder)", "Gone"]) == ["Series (Folder)"]
    assert context_menu.menu_presets(names, ["Gone"]) == names  # nothing left: all of them


INSTALLED = Path(r"C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge\main.py")


def menu_of(main_py: Path) -> str:
    return f'"C:\\x\\pythonw.exe" "{main_py}" --confirm --preset "Auto" "%1"'


def test_refresh_rewrites_the_menu_of_this_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[RegEntry] = []
    monkeypatch.setattr(context_menu, "installed_command", lambda: menu_of(INSTALLED))
    monkeypatch.setattr(context_menu, "install", lambda entries: written.extend(entries))
    assert context_menu.refresh(["Auto", "New one"], [], INSTALLED)
    verbs = [e.values.get("MUIVerb") for e in written]
    assert "Forge: Auto" in verbs and "Forge: New one" in verbs


@pytest.mark.parametrize("launcher", [Path(r"D:\work\MediaForge\main.py"), None])
def test_refresh_never_takes_over_another_copys_menu(
    monkeypatch: pytest.MonkeyPatch, launcher: Path | None
) -> None:
    # Review I4: a development copy (main.py elsewhere, or python -m mediaforge) used to
    # rewrite the installed copy's menu to point at itself at every start.
    written: list[RegEntry] = []
    monkeypatch.setattr(context_menu, "installed_command", lambda: menu_of(INSTALLED))
    monkeypatch.setattr(context_menu, "install", lambda entries: written.extend(entries))
    assert not context_menu.refresh(["Auto"], [], launcher)
    assert written == []


def test_refresh_leaves_a_menu_that_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[RegEntry] = []
    monkeypatch.setattr(context_menu, "installed_command", lambda: None)
    monkeypatch.setattr(context_menu, "install", lambda entries: written.extend(entries))
    assert not context_menu.refresh(["Auto"], [], None)
    assert written == []


def test_default_icon_is_the_bundled_ico() -> None:
    from pathlib import Path

    icon = Path(context_menu.default_icon())
    assert icon.name == "MediaForge.ico" and icon.is_file()
