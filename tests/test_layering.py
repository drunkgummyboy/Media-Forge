"""The non-UI layers must stay importable and testable without a display."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mediaforge"
NON_UI = ["core", "api", "db", "models", "platform"]
FORBIDDEN = ("tkinter", "customtkinter", "tkinterdnd2", "async_tkinter_loop", "mediaforge.ui")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_non_ui_packages_never_import_tk() -> None:
    offenders: list[str] = []
    for pkg in NON_UI:
        for file in (SRC / pkg).rglob("*.py"):
            for name in _imported_modules(file):
                if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                    offenders.append(f"{file.relative_to(SRC)} imports {name}")
    assert offenders == []


def test_guard_actually_detects_imports(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_text("import customtkinter\nfrom mediaforge.ui import thing\n", encoding="utf-8")
    assert {"customtkinter", "mediaforge.ui"} <= _imported_modules(sample)
