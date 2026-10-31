"""Launch-time package check: install what pyproject.toml lists that is missing or too old.

pip installs into the Python running MediaForge (the install's private `.venv`). Ported from
AirGrabber's app/bootstrap.py. This runs *before* the check has passed, so it must stay
standard-library only. The progress window lives in ui/bootstrap_window.py and is passed in by
the launcher (this layer never imports Tk).

Explorer starts one process per selected file, so right after an update that added a package
several launches can find it missing at once: a named "setup" mutex lets exactly one of them run
pip while the others wait, re-check and carry on.
"""

from __future__ import annotations

import ctypes
import getpass
import importlib
import importlib.metadata
import importlib.util
import logging
import operator
import os
import re
import site
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mediaforge.platform.update_files import version_tuple

log = logging.getLogger("mediaforge.setup")

SETUP_WAIT_S = 600.0  # how long a launch waits for another one that is installing packages
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash for pip
_POLL_S = 0.15

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CLAUSE = re.compile(r"(==|!=|>=|<=|~=|>|<)\s*(\S+)")
_RELEASE = re.compile(r"\d+(?:\.\d+)*")
_COMPARE: dict[str, Callable[[tuple[int, ...], tuple[int, ...]], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "~=": operator.ge,  # compatible release: only its lower bound is checked here
}


@dataclass(frozen=True, slots=True)
class Requirement:
    name: str
    clauses: tuple[tuple[str, str], ...] = ()  # e.g. ((">=", "2.14"),)

    @property
    def specifier(self) -> str:
        return ",".join(op + version for op, version in self.clauses)


@dataclass(frozen=True, slots=True)
class Problem:
    requirement: Requirement
    installed: str | None  # None: not installed at all

    def __str__(self) -> str:
        if self.installed is None:
            return f"{self.requirement.name} (not installed)"
        return f"{self.requirement.name} {self.installed} (needs {self.requirement.specifier})"


type Lookup = Callable[[str], str | None]
type RunInstall = Callable[[Sequence[Sequence[str]], Path, Sequence[Problem]], int | None]
type ReportFailure = Callable[[Sequence[Problem], Path], None]


def parse_requirement(text: str) -> Requirement | None:
    """One `name[extras] op version, ...` entry; environment markers and unreadable clauses
    (URLs, wildcards) are dropped, which leaves only a presence check for pip to judge."""
    line = text.split("#", 1)[0].split(";", 1)[0].strip()
    name = _NAME.match(line)
    if not line or name is None:
        return None
    rest = re.sub(r"^\s*\[[^\]]*\]", "", line[name.end() :])
    clauses: list[tuple[str, str]] = []
    for part in rest.split(","):
        clause = _CLAUSE.fullmatch(part.strip())
        if clause and _RELEASE.fullmatch(clause[2]):
            clauses.append((clause[1], clause[2]))
    return Requirement(name[0], tuple(clauses))


def requirements(pyproject: Path) -> list[Requirement]:
    """`[project].dependencies` from pyproject.toml."""
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    found: list[Requirement] = []
    for entry in data.get("project", {}).get("dependencies", []):
        if isinstance(entry, str) and (req := parse_requirement(entry)) is not None:
            found.append(req)
    return found


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def unmet(reqs: Sequence[Requirement], lookup: Lookup | None = None) -> list[Problem]:
    find = lookup or installed_version
    problems: list[Problem] = []
    for req in reqs:
        installed = find(req.name)
        if installed is None or not _satisfies(installed, req):
            problems.append(Problem(req, installed))
    return problems


def _satisfies(installed: str, req: Requirement) -> bool:
    have = version_tuple(installed)
    if have is None:
        return True  # unreadable version string: leave it to pip
    for op, wanted in req.clauses:
        want = version_tuple(wanted)
        if want is not None and not _COMPARE[op](have, want):
            return False
    return True


def pip_commands(reqs: Sequence[Requirement]) -> list[list[str]]:
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    pip += [req.name + req.specifier for req in reqs]
    if importlib.util.find_spec("pip") is None:
        return [[sys.executable, "-m", "ensurepip", "--upgrade"], pip]
    return [pip]


def refresh_import_paths() -> None:
    """Let this process see what pip just installed."""
    if site.ENABLE_USER_SITE:
        user_site = site.getusersitepackages()
        if user_site not in sys.path and os.path.isdir(user_site):
            site.addsitedir(user_site)
    importlib.invalidate_caches()


def ensure_requirements(
    pyproject: Path,
    log_file: Path,
    *,
    run_install: RunInstall | None = None,
    report_failure: ReportFailure | None = None,
    lookup: Lookup | None = None,
    mutex_name: str | None = None,
) -> bool:
    """Install missing or outdated packages. False: they are still missing, the app can't start.

    Only the launch holding the setup lock runs pip. One that waited behind a launch whose
    install just failed doesn't try again (that one already showed the error), and one that
    never gets the lock never runs pip at all.
    """
    started = time.time()
    try:
        reqs = requirements(pyproject)
    except (OSError, tomllib.TOMLDecodeError):
        log.warning("Cannot read %s; skipping the package check", pyproject)
        return True
    if not unmet(reqs, lookup):
        return True  # the usual case: a few milliseconds, no mutex

    with _SetupLock(mutex_name or f"MediaForge-setup-{_user()}") as acquired:
        if not acquired:
            log.warning("Another MediaForge is still installing packages; giving up waiting")
            return False
        problems = unmet(reqs, lookup)  # another launch may have installed them meanwhile
        if not problems:
            return True
        failed = log_file.with_name(log_file.name + ".failed")
        if _changed_since(failed, started):
            log.warning("Installing packages failed in another launch just now; not again")
            return False
        log.warning("Installing missing packages: %s", "; ".join(map(str, problems)))
        install = run_install or run_in_console
        returncode = install(pip_commands([p.requirement for p in problems]), log_file, problems)
        if returncode is None:
            log.info("Package installation cancelled")
            return False
        refresh_import_paths()
        remaining = unmet(reqs, lookup)
        if not remaining:
            log.info("Installed the missing packages")
            failed.unlink(missing_ok=True)
            return True
        if returncode == 0 and all(p.installed is not None for p in remaining):
            # pip says everything is satisfied; on odd version strings the two can disagree.
            outdated = "; ".join(map(str, remaining))
            log.warning("pip finished, but these still look outdated: %s", outdated)
            return True
        log.error(
            "Package installation failed (exit code %s). Last pip output:\n%s",
            returncode,
            tail(log_file),
        )
        try:
            failed.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        except OSError:
            pass
        (report_failure or _print_failure)(remaining, log_file)
        return False


def _changed_since(path: Path, moment: float) -> bool:
    try:
        return path.stat().st_mtime >= moment
    except OSError:
        return False


def failure_message(problems: Sequence[Problem], log_file: Path) -> str:
    return (
        "MediaForge could not install the parts it needs:\n\n"
        + "\n".join(f"•  {problem}" for problem in problems)
        + "\n\nCheck your internet connection and start MediaForge again. If it keeps "
        + "failing, paste the install line again.\n"
        + f"pip's output is in:\n{log_file}"
    )


def _print_failure(problems: Sequence[Problem], log_file: Path) -> None:
    to_console(failure_message(problems, log_file))


def to_console(text: str) -> None:
    """Write to the console when there is one (sys.stderr is None under pythonw.exe)."""
    if sys.stderr is not None:
        sys.stderr.write(text + "\n")


class Installer:
    """Runs commands one after another, stopping at the first failure; output goes to `log_file`.

    Non-blocking: call `poll()` repeatedly (from a Tk `after` loop or a sleep loop) until
    `finished`.
    """

    def __init__(self, commands: Sequence[Sequence[str]], log_file: Path) -> None:
        self._queue = [list(command) for command in commands]
        self._log_file = log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")
        self._reader = log_file.open(encoding="utf-8", errors="replace")
        self._partial = ""
        self._process: subprocess.Popen[bytes] | None = None
        self.returncode: int | None = None  # once finished: 0, or the failing command's exit code

    @property
    def finished(self) -> bool:
        return self.returncode is not None

    def poll(self) -> list[str]:
        """Advance the queue and return the output lines written since the previous call."""
        if self._process is not None and (code := self._process.poll()) is not None:
            self._process = None
            if code != 0:
                self.returncode = code
        if self._process is None and self.returncode is None:
            if self._queue:
                self._start(self._queue.pop(0))
            else:
                self.returncode = 0
        return self._read_lines()

    def cancel(self) -> None:
        self._queue.clear()
        if self._process is not None:
            self._process.kill()
            self._process.wait()
            self._process = None

    def close(self) -> None:
        self.cancel()
        self._reader.close()

    def _start(self, command: list[str]) -> None:
        with self._log_file.open("a", encoding="utf-8") as out:
            out.write("> " + subprocess.list2cmdline(command) + "\n")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            with self._log_file.open("ab") as out:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=_NO_WINDOW,
                )
        except OSError as exc:
            with self._log_file.open("a", encoding="utf-8") as out:
                out.write(f"Could not start it: {exc}\n")
            self.returncode = 1

    def _read_lines(self) -> list[str]:
        lines = (self._partial + self._reader.read()).split("\n")
        self._partial = "" if self.finished else lines.pop()
        return [line for line in lines if line.strip()]


def run_in_console(
    commands: Sequence[Sequence[str]], log_file: Path, problems: Sequence[Problem]
) -> int | None:
    """Run the install without a window (used when Tk can't open one)."""
    installer = Installer(commands, log_file)
    try:
        while True:
            echo(installer.poll())
            if installer.finished:
                return installer.returncode
            time.sleep(_POLL_S)
    except KeyboardInterrupt:
        installer.cancel()
        return None
    finally:
        installer.close()


def echo(lines: Sequence[str]) -> None:
    for line in lines:
        to_console(f"  {line}")


def tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no output)"


def _user() -> str:
    try:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
    except Exception:
        return "user"


class _SetupLock:
    """A per-user named mutex held while pip runs; `with` yields whether it was acquired."""

    _WAIT_OBJECT_0 = 0x0
    _WAIT_ABANDONED = 0x80  # the previous holder died: the mutex is ours now

    def __init__(self, name: str, timeout_s: float | None = None) -> None:
        self._name = "Local\\" + name
        self._timeout_ms = int((SETUP_WAIT_S if timeout_s is None else timeout_s) * 1000)
        self._handle = 0
        self._owned = False

    def __enter__(self) -> bool:
        if sys.platform != "win32":
            return True
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._k32 = k32
        self._handle = k32.CreateMutexW(None, False, self._name) or 0
        if not self._handle:
            return True  # can't coordinate; run pip anyway rather than refuse to start
        result = k32.WaitForSingleObject(self._handle, self._timeout_ms)
        self._owned = result in (self._WAIT_OBJECT_0, self._WAIT_ABANDONED)
        return self._owned

    def __exit__(self, *exc: object) -> None:
        if self._handle:
            if self._owned:
                self._k32.ReleaseMutex(self._handle)
            self._k32.CloseHandle(self._handle)
            self._handle = 0
