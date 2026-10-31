import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from mediaforge.platform.bootstrap import (
    Problem,
    Requirement,
    ensure_requirements,
    parse_requirement,
    pip_commands,
    requirements,
    unmet,
)

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def write_pyproject(tmp_path: Path, *deps: str) -> Path:
    path = tmp_path / "pyproject.toml"
    listed = ", ".join(f'"{d}"' for d in deps)
    path.write_text(f'[project]\nname = "x"\ndependencies = [{listed}]\n', encoding="utf-8")
    return path


def test_reads_dependencies_from_pyproject() -> None:
    names = [r.name for r in requirements(PYPROJECT)]
    assert names == [
        "customtkinter",
        "tkinterdnd2",
        "async-tkinter-loop",
        "aiohttp",
        "aiosqlite",
        "pydantic",
        "guessit",
        "pillow",
        "pystray",  # the tray icon (1.3): installed by the launcher on the next start
    ]
    assert requirements(PYPROJECT)[0].clauses == ((">=", "6.0"),)


def test_parse_requirement_skips_markers_and_extras() -> None:
    assert parse_requirement("pydantic[email] >=2.14, <3 ; python_version >= '3.13'") == (
        Requirement("pydantic", ((">=", "2.14"), ("<", "3")))
    )
    assert parse_requirement("   ") is None


def test_unmet_finds_missing_and_too_old() -> None:
    reqs = [Requirement("a", ((">=", "2.0"),)), Requirement("b"), Requirement("c", ((">=", "1"),))]
    found = {"a": "1.9", "c": "1.0.0"}
    problems = unmet(reqs, lookup=found.get)
    assert [(p.requirement.name, p.installed) for p in problems] == [("a", "1.9"), ("b", None)]
    assert str(problems[1]) == "b (not installed)"


def test_pip_commands_install_exactly_what_is_listed() -> None:
    [command] = pip_commands([Requirement("aiohttp", ((">=", "3.14"),)), Requirement("guessit")])
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[-2:] == ["aiohttp>=3.14", "guessit"]


def test_nothing_unmet_runs_no_pip(tmp_path: Path) -> None:
    calls: list[int] = []

    def run_install(
        commands: Sequence[Sequence[str]], log: Path, problems: Sequence[Problem]
    ) -> int:
        calls.append(1)
        return 0

    pyproject = write_pyproject(tmp_path, "a>=1")
    assert ensure_requirements(
        pyproject, tmp_path / "pip.log", run_install=run_install, lookup={"a": "1.2"}.get
    )
    assert calls == []


def test_unreadable_pyproject_lets_the_app_start(tmp_path: Path) -> None:
    assert ensure_requirements(tmp_path / "missing.toml", tmp_path / "pip.log")


def test_failed_install_reports_and_returns_false(tmp_path: Path) -> None:
    reported: list[list[str]] = []
    pyproject = write_pyproject(tmp_path, "a>=1")
    ok = ensure_requirements(
        pyproject,
        tmp_path / "pip.log",
        run_install=lambda commands, log, problems: 1,
        report_failure=lambda problems, log: reported.append([str(p) for p in problems]),
        lookup={}.get,
        mutex_name=f"MediaForge-test-{uuid.uuid4().hex}",
    )
    assert not ok
    assert reported == [["a (not installed)"]]


def test_only_one_process_runs_pip(tmp_path: Path) -> None:
    # Review focus 1: Explorer starts many launches at once right after an update.
    installed: dict[str, str] = {}
    calls: list[str] = []
    pyproject = write_pyproject(tmp_path, "newpkg>=1")
    name = f"MediaForge-test-{uuid.uuid4().hex}"

    def run_install(
        commands: Sequence[Sequence[str]], log: Path, problems: Sequence[Problem]
    ) -> int:
        calls.append(threading.current_thread().name)
        time.sleep(0.3)
        installed["newpkg"] = "1.0"
        return 0

    results: list[bool] = []

    def launch() -> None:
        results.append(
            ensure_requirements(
                pyproject,
                tmp_path / "pip.log",
                run_install=run_install,
                lookup=installed.get,
                mutex_name=name,
            )
        )

    threads = [threading.Thread(target=launch, name=f"launch-{i}") for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(calls) == 1
    assert results == [True, True, True]


def test_failed_install_in_a_burst_runs_pip_and_reports_once(tmp_path: Path) -> None:
    # Review I6: with no internet, 8 right-clicked files meant 8 pip runs and 8 error windows.
    calls: list[str] = []
    reports: list[str] = []
    pyproject = write_pyproject(tmp_path, "newpkg>=1")
    name = f"MediaForge-test-{uuid.uuid4().hex}"

    def run_install(
        commands: Sequence[Sequence[str]], log: Path, problems: Sequence[Problem]
    ) -> int:
        calls.append(threading.current_thread().name)
        time.sleep(0.3)
        return 1

    results: list[bool] = []

    def launch() -> None:
        results.append(
            ensure_requirements(
                pyproject,
                tmp_path / "pip.log",
                run_install=run_install,
                report_failure=lambda problems, log: reports.append("shown"),
                lookup={}.get,
                mutex_name=name,
            )
        )

    threads = [threading.Thread(target=launch) for _ in range(3)]
    for t in threads:
        t.start()
        time.sleep(0.05)  # Explorer starts them one after another
    for t in threads:
        t.join(10)
    assert len(calls) == 1 and reports == ["shown"]
    assert results == [False, False, False]


def test_a_launch_that_never_gets_the_lock_does_not_run_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mediaforge.platform import bootstrap

    monkeypatch.setattr(bootstrap, "SETUP_WAIT_S", 0.2)
    name = f"MediaForge-test-{uuid.uuid4().hex}"
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with bootstrap._SetupLock(name):
            holding.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait(5)
    calls: list[int] = []
    try:
        ok = ensure_requirements(
            write_pyproject(tmp_path, "newpkg>=1"),
            tmp_path / "pip.log",
            run_install=lambda commands, log, problems: calls.append(1) or 0,
            lookup={}.get,
            mutex_name=name,
        )
    finally:
        release.set()
        holder.join(5)
    assert ok is False and calls == []
