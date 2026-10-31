"""Command-line arguments (also what the Explorer right-click menu passes in)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel


class LaunchArgs(BaseModel):
    paths: list[Path] = []
    preset: str | None = None
    confirm: bool = False
    self_test: bool = False
    install_menu: bool = False  # install.ps1
    remove_menu: bool = False  # uninstall.ps1


def parse_args(argv: Sequence[str]) -> LaunchArgs:
    parser = argparse.ArgumentParser(prog="MediaForge", add_help=False)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--preset")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--auto-run", action="store_true")  # legacy menu entries
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--install-menu", action="store_true")
    parser.add_argument("--remove-menu", action="store_true")
    ns, _unknown = parser.parse_known_args(list(argv))
    return LaunchArgs(
        paths=[Path(p) for p in ns.paths],
        preset=ns.preset,
        confirm=bool(ns.confirm or ns.auto_run),
        self_test=bool(ns.self_test),
        install_menu=bool(ns.install_menu),
        remove_menu=bool(ns.remove_menu),
    )
