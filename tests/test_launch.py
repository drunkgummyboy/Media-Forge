"""The launcher: who runs the package check, the app, and an update, in which order."""

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mediaforge import launch
from mediaforge.launch import LaunchHooks

ROOT = Path(__file__).resolve().parents[1]


class FakePrimary:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def close(self) -> None:
        self.calls.append("close")


def hooks(
    calls: list[str],
    *,
    primary: bool = True,
    restart: bool = False,
    applied: str | None = "1.2.0",
    requirements_ok: bool = True,
    crash: bool = False,
    check_crash: bool = False,
    running: bool = False,
    sends_ok: bool = True,
    lock_free_after: int = 0,
) -> LaunchHooks:
    """Fakes for every step. `running`: another MediaForge holds the lock; `sends_ok`: it
    takes hand-overs; `lock_free_after`: that window lets go of the lock after so many
    hand-over attempts (0: never, when `running`)."""
    fake = FakePrimary(calls)
    sends = [0]

    def lock_held() -> bool:
        return running and (lock_free_after == 0 or sends[0] < lock_free_after)

    def ensure_requirements() -> bool:
        calls.append("requirements")
        if check_crash:
            raise RuntimeError("broken package")
        return requirements_ok

    def try_become_primary(key: Path) -> Any:
        return fake if primary and not lock_held() else None

    def send(msg: Any, key: Path) -> bool:
        calls.append("send")
        sends[0] += 1
        return sends_ok

    def run_app(args: Any, p: Any) -> Any:
        calls.append("run")
        if crash:
            raise RuntimeError("boom")
        return SimpleNamespace(restart=restart)

    def apply_staged(install_dir: Path) -> str | None:
        calls.append("apply")
        return applied

    return LaunchHooks(
        ensure_requirements=ensure_requirements,
        try_become_primary=try_become_primary,
        send_to_primary=send,
        run_app=run_app,
        apply_staged=apply_staged,
        ensure_uninstall_entry=lambda d, v: calls.append(f"entry {v}"),
        relaunch=lambda: calls.append("relaunch"),
        self_test=lambda: 0,
        instance_running=lock_held,
        report_crash=lambda exc: calls.append(f"crash {exc}"),
    )


@pytest.fixture
def install_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Programs" / "MediaForge"
    root.mkdir(parents=True)
    return root


def test_secondary_hands_over_and_never_applies_updates(install_dir: Path) -> None:
    calls: list[str] = []
    assert launch.main(["a.mkv"], install_dir=install_dir, hooks=hooks(calls, primary=False)) == 0
    assert calls == ["requirements", "send"]


def test_primary_applies_staged_update_before_releasing_the_lock(install_dir: Path) -> None:
    calls: list[str] = []
    assert launch.main([], install_dir=install_dir, hooks=hooks(calls)) == 0
    assert calls == ["requirements", "run", "apply", "entry 1.2.0", "close"]


def test_nothing_staged_writes_no_entry(install_dir: Path) -> None:
    calls: list[str] = []
    launch.main([], install_dir=install_dir, hooks=hooks(calls, applied=None))
    assert calls == ["requirements", "run", "apply", "close"]


def test_development_copy_never_applies(install_dir: Path) -> None:
    (install_dir / "tests").mkdir()
    calls: list[str] = []
    launch.main([], install_dir=install_dir, hooks=hooks(calls))
    assert calls == ["requirements", "run", "close"]


def test_restart_outcome_relaunches_after_release(install_dir: Path) -> None:
    calls: list[str] = []
    launch.main([], install_dir=install_dir, hooks=hooks(calls, restart=True))
    assert calls[-2:] == ["close", "relaunch"]


def test_missing_packages_stop_the_launch(install_dir: Path) -> None:
    calls: list[str] = []
    assert launch.main([], install_dir=install_dir, hooks=hooks(calls, requirements_ok=False)) == 1
    assert calls == ["requirements"]


def test_a_crash_is_reported_and_the_update_and_lock_still_happen(install_dir: Path) -> None:
    # Review I5: under pythonw a crash used to vanish without a word.
    calls: list[str] = []
    assert launch.main([], install_dir=install_dir, hooks=hooks(calls, crash=True)) == 1
    assert calls == ["requirements", "run", "apply", "entry 1.2.0", "close", "crash boom"]


def test_a_crash_in_the_package_check_is_reported(install_dir: Path) -> None:
    calls: list[str] = []
    assert launch.main([], install_dir=install_dir, hooks=hooks(calls, check_crash=True)) == 1
    assert calls == ["requirements", "crash broken package"]


def test_default_crash_report_logs_and_shows_a_message(
    monkeypatch: pytest.MonkeyPatch, mediaforge_home: Path
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(launch, "_show_error", shown.append)
    try:
        raise ValueError("the details")
    except ValueError as exc:
        launch.report_crash(exc)
    log = (mediaforge_home / "logs" / "mediaforge.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "ValueError: the details" in log
    assert len(shown) == 1 and "mediaforge.log" in shown[0] and "install" in shown[0]


def test_hand_over_skips_the_package_check_while_a_window_is_open(install_dir: Path) -> None:
    # Ruling disagreement: no package check (and never pip) next to a running MediaForge.
    calls: list[str] = []
    steps = hooks(calls, primary=False, running=True)
    assert launch.main(["a.mkv"], install_dir=install_dir, hooks=steps) == 0
    assert calls == ["send"]


def test_launch_during_shutdown_waits_for_the_lock_and_opens_the_file(install_dir: Path) -> None:
    # Review I3: the old window stopped listening but still held the lock (swapping in an
    # update); the new launch must not give up, but take over once the lock is free.
    calls: list[str] = []
    steps = hooks(calls, running=True, sends_ok=False, lock_free_after=3, applied=None)
    assert launch.main(["a.mkv"], install_dir=install_dir, hooks=steps) == 0
    assert calls == ["send", "send", "send", "requirements", "run", "apply", "close"]


def test_launch_gives_up_after_the_deadline(install_dir: Path) -> None:
    calls: list[str] = []
    steps = hooks(calls, running=True, sends_ok=False)
    assert launch.main(["a.mkv"], install_dir=install_dir, hooks=steps, handover_s=0.3) == 1
    assert set(calls) == {"send"}  # no package check, and pip never runs next to that window


def test_installed_apps_entry_is_refreshed_even_without_a_staged_update(
    install_dir: Path,
) -> None:
    # Review minor 8: after Settings' "Update now" the entry kept the old version number.
    (install_dir / "src" / "mediaforge").mkdir(parents=True)
    (install_dir / "src" / "mediaforge" / "__init__.py").write_text(
        '__version__ = "1.3.0"\n', encoding="utf-8"
    )
    calls: list[str] = []
    launch.main([], install_dir=install_dir, hooks=hooks(calls, applied=None))
    assert calls == ["requirements", "run", "apply", "entry 1.3.0", "close"]


def test_instance_probe_sees_a_held_lock() -> None:
    from mediaforge.platform.single_instance import try_become_primary

    name = f"probe-test-{uuid.uuid4().hex}"
    assert not launch.instance_running(name)
    primary = try_become_primary(Path(tempfile.mkdtemp()) / "key", name=name)
    assert primary is not None
    try:
        assert launch.instance_running(name)
    finally:
        primary.close()
    assert not launch.instance_running(name)


def test_self_test_flag_runs_only_the_self_test(install_dir: Path) -> None:
    calls: list[str] = []
    assert launch.main(["--self-test"], install_dir=install_dir, hooks=hooks(calls)) == 0
    assert calls == ["requirements"]


def test_main_py_imports_nothing_third_party() -> None:
    # Before the package check has run, nothing third-party may be imported: it may be missing.
    script = (
        "import runpy, sys\n"
        f"runpy.run_path({str(ROOT / 'main.py')!r}, run_name='not_main')\n"
        "import mediaforge.launch\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'pydantic', 'aiohttp', 'aiosqlite', 'customtkinter', 'PIL', 'guessit', 'tkinter',"
        " 'tkinterdnd2', 'async_tkinter_loop'})\n"
        "print(bad)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


async def test_install_menu_flag_installs_all_presets(monkeypatch: pytest.MonkeyPatch) -> None:
    from mediaforge.app import menu_action
    from mediaforge.platform import context_menu

    written: list[Any] = []
    monkeypatch.setattr(context_menu, "install", lambda entries: written.extend(entries))
    assert await menu_action(install=True) == 0
    verbs = [e.values.get("MUIVerb") for e in written]
    assert "Forge: Auto" in verbs and "Forge: Series (Folder)" in verbs
    removed: list[int] = []
    monkeypatch.setattr(context_menu, "uninstall", lambda: removed.append(1))
    assert await menu_action(install=False) == 0
    assert removed == [1]


def test_menu_flags_run_only_the_menu_step(install_dir: Path) -> None:
    calls: list[str] = []
    steps = hooks(calls)
    steps.menu = lambda install: (calls.append(f"menu {install}"), 0)[1]
    assert launch.main(["--install-menu"], install_dir=install_dir, hooks=steps) == 0
    assert launch.main(["--remove-menu"], install_dir=install_dir, hooks=steps) == 0
    assert calls == ["requirements", "menu True", "requirements", "menu False"]


async def test_install_menu_keeps_the_presets_picked_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reinstall must not undo the owner's choice in Settings.
    from mediaforge.app import menu_action
    from mediaforge.db.database import Database
    from mediaforge.db.presets import PresetRepo
    from mediaforge.db.settings import SettingsRepo
    from mediaforge.platform import context_menu
    from mediaforge.platform.paths import app_paths

    paths = app_paths()
    paths.ensure()
    db = await Database.open(paths.db_file)
    await PresetRepo(db).seed_defaults_if_empty()
    settings = SettingsRepo(db)
    await settings.save(
        (await settings.load()).model_copy(update={"context_menu_presets": ["Auto"]})
    )
    await db.close()
    written: list[Any] = []
    monkeypatch.setattr(context_menu, "install", lambda entries: written.extend(entries))
    assert await menu_action(install=True) == 0
    verbs = {e.values.get("MUIVerb") for e in written} - {"MediaForge", None}
    assert verbs == {"Forge: Auto"}
    db = await Database.open(paths.db_file)
    assert (await SettingsRepo(db).load()).context_menu_presets == ["Auto"]
    await db.close()


def test_power_cut_repair_waits_while_another_mediaforge_holds_the_lock(tmp_path: Path) -> None:
    # A right-click arriving while the open window swaps in an update must not "repair" the
    # swap that is happening at that very moment.
    import runpy

    from mediaforge.platform.single_instance import try_become_primary

    root = tmp_path / "MediaForge"
    (root / ".update" / "old").mkdir(parents=True)
    (root / ".update" / "old" / "main.py").write_text("# old\n", encoding="utf-8")
    (root / "main.py").write_text("# new\n", encoding="utf-8")
    (root / "src").mkdir()
    recover = runpy.run_path(str(ROOT / "main.py"), run_name="not_main")["recover"]
    name = f"recover-test-{uuid.uuid4().hex}"
    primary = try_become_primary(tmp_path / "key", name=name)
    assert primary is not None
    try:
        recover(root, lock_name=name)
        assert (root / "main.py").read_text(encoding="utf-8") == "# new\n"  # left alone
        assert (root / ".update" / "old").is_dir()
    finally:
        primary.close()
