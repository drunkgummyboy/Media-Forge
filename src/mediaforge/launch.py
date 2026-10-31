"""Start MediaForge: hand over to an open window, or check the packages and run the app, then
swap in a downloaded update once the app has shut down.

Started by `main.py` (installed copies, shortcuts, the right-click menu) or `python -m mediaforge`.
Only standard-library modules are imported at module level: until `ensure_requirements` has run,
the third-party packages may be missing (an update can bring new ones).

The update is swapped in while this process still holds the single-instance lock, so no other
MediaForge starts from half-replaced files. Launches arriving meanwhile keep offering their files
and wait for the lock (up to HANDOVER_S); they never run the package check or pip while another
MediaForge holds it.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaforge.platform.instance_name import instance_running
from mediaforge.platform.paths import app_paths
from mediaforge.platform.update_files import installed_version, is_development_copy

log = logging.getLogger("mediaforge.update")

LAUNCHER: Path | None = None  # main.py, when MediaForge was started through it
# How long a launch keeps trying to hand its files to a window that is shutting down (and
# maybe swapping in an update) before it gives up; well beyond a slow swap.
HANDOVER_S = 60.0
SEND_TIMEOUT_S = 2.0  # one hand-over attempt; then the lock is tried again
_ACTIONS = ("--self-test", "--install-menu", "--remove-menu")


@dataclass
class LaunchHooks:
    """What `main` does at each step; tests replace them."""

    ensure_requirements: Callable[[], bool]
    try_become_primary: Callable[[Path], Any]  # a Primary, or None when one is already open
    send_to_primary: Callable[[Any, Path], bool]
    run_app: Callable[[Any, Any], Any]  # returns app.RunOutcome
    apply_staged: Callable[[Path], str | None]
    ensure_uninstall_entry: Callable[[Path, str], None]
    relaunch: Callable[[], None]
    self_test: Callable[[], int]
    menu: Callable[[bool], int] = lambda install: 0  # --install-menu / --remove-menu
    instance_running: Callable[[], bool] = lambda: False
    report_crash: Callable[[BaseException], None] = lambda exc: None


def main(
    argv: Sequence[str],
    *,
    install_dir: Path,
    hooks: LaunchHooks | None = None,
    handover_s: float = HANDOVER_S,
) -> int:
    steps = hooks or default_hooks(install_dir)
    deadline = time.monotonic() + handover_s
    if not any(arg in _ACTIONS for arg in argv) and steps.instance_running():
        # Another MediaForge holds the lock: hand the files over, without a package check (it
        # runs from the same environment, and pip must never run next to it). It may be
        # shutting down and swapping in an update: then wait until it lets go of the lock.
        handed = _hand_over_while_running(argv, steps, deadline)
        if handed is not None:
            return 0 if handed else 1
    try:
        if not steps.ensure_requirements():
            return 1
    except Exception as exc:
        steps.report_crash(exc)
        return 1
    # The packages are there now.
    from mediaforge.platform.cli import parse_args
    from mediaforge.platform.single_instance import InstanceMessage

    args = parse_args(list(argv))
    if args.self_test:
        return steps.self_test()
    if args.install_menu or args.remove_menu:
        return steps.menu(args.install_menu)
    paths = app_paths()
    paths.ensure()
    msg = _message(args, InstanceMessage)
    # Two launches can find the lock free at once: the one that loses hands over.
    while (primary := steps.try_become_primary(paths.instance_key_file)) is None:
        if steps.send_to_primary(msg, paths.instance_key_file):
            return 0
        if time.monotonic() >= deadline:
            log.warning("No MediaForge window took the files and none could be opened")
            return 1
    restart = False
    crash: BaseException | None = None
    try:
        outcome = steps.run_app(args, primary)
        restart = bool(getattr(outcome, "restart", False))
    except Exception as exc:
        crash = exc
    finally:
        try:
            if not is_development_copy(install_dir):
                # Also after Settings' "Update now", which swapped the files in while running.
                version = steps.apply_staged(install_dir) or installed_version(install_dir)
                if version is not None:
                    steps.ensure_uninstall_entry(install_dir, version)
        finally:
            primary.close()
    if crash is not None:
        steps.report_crash(crash)
        return 1
    if restart:
        steps.relaunch()
    return 0


def _message(args: Any, message_type: Any) -> Any:
    paths = [str(p) for p in args.paths]
    return message_type(paths=paths, preset=args.preset, confirm=args.confirm)


def _hand_over_while_running(
    argv: Sequence[str], steps: LaunchHooks, deadline: float
) -> bool | None:
    """Hand over to the MediaForge that holds the lock. True: it took the files; False: it
    never did before the deadline; None: the lock is free now (or the hand-over code can't
    load), so start normally."""
    try:
        from mediaforge.platform.cli import parse_args
        from mediaforge.platform.single_instance import InstanceMessage
    except ImportError:
        return None
    msg = _message(parse_args(list(argv)), InstanceMessage)
    key_file = app_paths().instance_key_file
    while True:
        if steps.send_to_primary(msg, key_file):
            return True
        if not steps.instance_running():
            return None
        if time.monotonic() >= deadline:
            log.warning("The open MediaForge didn't take the files")
            return False


def report_crash(exc: BaseException) -> None:
    """Write what went wrong to mediaforge.log and say so in a window (pythonw has no console)."""
    log_file = app_paths().logs_dir / "mediaforge.log"
    details = "".join(traceback.format_exception(exc))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR MediaForge couldn't run\n")
            out.write(details)
    except OSError:
        pass
    _show_error(
        "MediaForge ran into a problem and had to stop.\n\n"
        f"What happened is written in:\n{log_file}\n\n"
        "Start MediaForge again. If it keeps happening, paste the install line again "
        "(it repairs MediaForge and keeps your settings), or send that file to whoever "
        "helped you set it up."
    )


def _show_error(text: str) -> None:
    if sys.stderr is not None:
        sys.stderr.write(text + "\n")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("MediaForge", text, parent=root)
        root.destroy()
    except Exception:
        pass


def relaunch_command(install_dir: Path) -> list[str]:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else exe)
    if LAUNCHER is not None:
        return [runner, str(LAUNCHER)]
    return [runner, "-m", "mediaforge"]


def default_hooks(install_dir: Path) -> LaunchHooks:
    def ensure_requirements() -> bool:
        from mediaforge.platform.bootstrap import ensure_requirements as check

        def with_window(*args: Any) -> int | None:
            from mediaforge.ui.bootstrap_window import run_with_window

            return run_with_window(*args)

        def report(*args: Any) -> None:
            from mediaforge.ui.bootstrap_window import report_failure

            report_failure(*args)

        log_file = app_paths().logs_dir / "pip.log"
        return check(
            install_dir / "pyproject.toml", log_file, run_install=with_window, report_failure=report
        )

    def try_become_primary(key_file: Path) -> Any:
        from mediaforge.platform.single_instance import try_become_primary as become

        return become(key_file)

    def send_to_primary(msg: Any, key_file: Path) -> bool:
        from mediaforge.platform.single_instance import send_to_primary as send

        return send(msg, key_file, timeout_s=SEND_TIMEOUT_S)

    def run_app(args: Any, primary: Any) -> Any:
        import asyncio

        from mediaforge.app import run

        return asyncio.run(run(args, primary))

    def apply_staged(folder: Path) -> str | None:
        from mediaforge.platform.update_files import apply_staged as apply

        with _update_log():
            return apply(folder)

    def ensure_uninstall_entry(folder: Path, version: str) -> None:
        from mediaforge.platform.install_info import ensure_uninstall_entry as ensure

        ensure(folder, version)

    def relaunch() -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(relaunch_command(install_dir), cwd=install_dir, creationflags=flags)

    def self_test() -> int:
        import asyncio

        from mediaforge.app import self_test as test

        return asyncio.run(test())

    def menu(install: bool) -> int:
        import asyncio

        from mediaforge.app import menu_action

        return asyncio.run(menu_action(install=install))

    return LaunchHooks(
        ensure_requirements=ensure_requirements,
        try_become_primary=try_become_primary,
        send_to_primary=send_to_primary,
        run_app=run_app,
        apply_staged=apply_staged,
        ensure_uninstall_entry=ensure_uninstall_entry,
        relaunch=relaunch,
        self_test=self_test,
        menu=menu,
        instance_running=instance_running,
        report_crash=report_crash,
    )


class _update_log:  # noqa: N801 - used as a context manager
    """The app's log is closed by now: send the update step's messages to the same file."""

    def __enter__(self) -> None:
        logs = app_paths().logs_dir
        self._handler: logging.Handler | None = None
        try:
            logs.mkdir(parents=True, exist_ok=True)
            self._handler = logging.FileHandler(logs / "mediaforge.log", encoding="utf-8")
        except OSError:
            return
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        for name in ("mediaforge.update", "mediaforge.install"):
            logging.getLogger(name).addHandler(self._handler)
            logging.getLogger(name).setLevel(logging.INFO)

    def __exit__(self, *exc: object) -> None:
        if self._handler is not None:
            for name in ("mediaforge.update", "mediaforge.install"):
                logging.getLogger(name).removeHandler(self._handler)
            self._handler.close()
