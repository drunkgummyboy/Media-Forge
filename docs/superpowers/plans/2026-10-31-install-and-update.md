# Install and Update Like AirGrabber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MediaForge installs from one PowerShell line, updates itself from
github.com/drunkgummyboy/Media-Forge and uninstalls from Windows Settings › Apps, exactly like
AirGrabber, running from source in a private virtual environment.

**Architecture:** A stdlib-only launcher (`main.py`) checks packages, runs the existing app and,
after shutdown, swaps in a staged update while still holding the single-instance lock. Update
logic is split into network (`api/updates.py`, aiohttp) and stdlib-only file work
(`platform/update_files.py`). `install.ps1` / `uninstall.ps1` are ports of AirGrabber's.

**Tech Stack:** Python 3.13, aiohttp, pydantic, tomllib, ctypes/winreg; Windows PowerShell 5.1.

**Spec:** `docs/superpowers/specs/2026-10-31-install-and-update-design.md`

**Source to port:** the AirGrabber project folder — `install.ps1`,
`app/uninstall.ps1`, `app/services/updates.py`, `app/services/install_info.py`,
`app/bootstrap.py`, `main.py` (`_apply_staged_update`, `_relaunch`), `app/ui/main_window.py`
(`_check_for_update`, `_stage_update`). Read each before porting it; keep its hard-won details
(rename retries, read-only clean-up, winget/python.org fallbacks, failure dialogs), drop
everything AirGrabber-specific (qBittorrent, Prowlarr, i18n `t()`, AppUserModelID, tray).

## Global Constraints

- Windows 10/11, current user only: HKCU, `%LOCALAPPDATA%`, never an admin prompt.
- Install folder default `%LOCALAPPDATA%\Programs\MediaForge`; app entries exactly
  `("main.py", "pyproject.toml", "src")`; packages in `<install>\.venv`; staging in `<install>\.update`.
- Data folders unchanged (`%APPDATA%\MediaForge`, `%LOCALAPPDATA%\MediaForge`,
  `MEDIAFORGE_HOME` override); install, update and reinstall never touch them.
- GitHub: repo `drunkgummyboy/Media-Forge`, branch `main`; version URL
  `https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/main/src/mediaforge/__init__.py`;
  archive URL `https://codeload.github.com/drunkgummyboy/Media-Forge/zip/refs/heads/main`;
  archive limit 100 MB.
- Only a strictly higher `__version__` is offered; a folder with `tests\` or `.git` is a
  development copy and never stages, installs, applies or uninstalls itself.
- `install.ps1`, `uninstall.ps1`: Windows PowerShell 5.1, `irm | iex`-safe: no `&&`, ternaries,
  `??`, no `exit`, ASCII only.
- Uninstall entry key `HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaForge`,
  Publisher `drunkgummyboy`, URL `https://github.com/drunkgummyboy/Media-Forge`.
- New setting `auto_update: bool = True`. First published version `1.1.0`.
- Tests never touch the real registry, real install folder, shortcuts or GitHub.
- Never print, log or commit the TMDB key; `mediaforge_config.json` / `mediaforge_undo.json`
  stay untracked.
- Everything in `CLAUDE.md` still holds (layering test, one asyncio loop, light imports for
  secondary processes, `prepare_popup`, plain language for the owner).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; this repo's
  `user.email` is `beau_spiessens@hotmail.com`.

## Review Focus

1. **Right-click burst right after an update that added a dependency:** Explorer starts many
   processes at once; exactly one may run pip, the others wait and then hand over normally
   (test in Task 4).
2. **A swap interrupted half-way** (file locked by antivirus, power cut): the old version must
   still start, and the next start finishes or discards the update cleanly (Task 1).
3. **User names and folders with spaces or non-ASCII letters** (`C:\Users\Jürgen Smit\...`) in
   shortcuts, the menu command, pip and the uninstall string (Tasks 6 and 7).
4. **"Update now" clicked while Forge or Undo is running:** the restart waits, exactly like
   closing the window (Task 5).
5. **Python found is the Microsoft Store stub** (`WindowsApps\python.exe` that opens the Store):
   the installer must not pick it (Task 7, ported `Test-Python`).

---

### Task 1: Staging and swapping update files (`platform/update_files.py`)

**Files:**
- Create: `src/mediaforge/platform/update_files.py`
- Test: `tests/platform/test_update_files.py`

**Interfaces:**
- Produces: `APP_ENTRIES: tuple[str, ...]`, `STAGING_DIR = ".update"`, `READY_FILE = "ready"`,
  `version_tuple(v: str) -> tuple[int, ...] | None`, `parse_version(init_source: str) -> str | None`,
  `is_newer(latest: str, current: str) -> bool`, `is_development_copy(install_dir: Path) -> bool`,
  `stage_archive(data: bytes, install_dir: Path, *, newer_than: str | None = None) -> str`,
  `install_archive(data: bytes, install_dir: Path) -> str`,
  `apply_staged(install_dir: Path) -> str | None`, `class UpdateFileError(Exception)`.
  Stdlib only (the launcher imports it after shutdown).

Port the file-system half of AirGrabber's `updates.py` (from `install_archive` down, plus
`parse_version`/`is_newer`/`is_development_copy` and `bootstrap.version_tuple`). Version source is
`src/mediaforge/__init__.py` inside the archive's single top folder.

- [ ] **Step 1: Write the failing tests** — build zips in `tmp_path` shaped like GitHub's
  (`Media-Forge-main/main.py`, `pyproject.toml`, `src/mediaforge/__init__.py` with
  `__version__ = "1.2.0"`, plus a `tests/` folder that must NOT be copied):
  - `test_stage_then_apply_replaces_only_app_entries` — install dir has the three entries at 1.1.0
    plus `.venv/marker` and `notes.txt`; after `stage_archive` + `apply_staged` the entries hold
    1.2.0, `.venv/marker` and `notes.txt` are untouched, no `tests/` appeared, `.update` is gone,
    return value `"1.2.0"`.
  - `test_stage_refuses_not_newer` — `newer_than="1.2.0"` raises `UpdateFileError`; nothing changed.
  - `test_archive_missing_an_entry_is_refused` and `test_archive_with_two_top_folders_is_refused`.
  - `test_development_copy_is_detected` (`tests/` or `.git` present).
  - `test_interrupted_swap_leaves_a_working_copy` — monkeypatch the rename helper to fail on the
    second entry: after `apply_staged` raises/returns `None`, every app entry exists and holds one
    complete version (old), and a second `apply_staged` call doesn't crash.
  - `test_apply_without_ready_marker_discards_staging`.
  - `test_version_compare` — `is_newer("1.10.0", "1.9.0")`, not `("1.1.0", "1.1.0")`, not on garbage.
- [ ] **Step 2: Run** `PY -m pytest tests/platform/test_update_files.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** by porting; rename retries (5 attempts) and read-only clean-up kept.
- [ ] **Step 4: Run** the same command — Expected: all pass.
- [ ] **Step 5: Commit** `feat: stage and apply update files`.

### Task 2: Update service (`api/updates.py`)

**Files:**
- Create: `src/mediaforge/models/update.py`, `src/mediaforge/api/updates.py`,
  `tests/helpers/fake_github.py`
- Test: `tests/api/test_updates.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `class UpdateInfo(BaseModel)` (`current`, `latest`, `available`, `installable`);
  `class UpdateError(Exception)` (message is plain English for the UI);
  `UpdateService(session: aiohttp.ClientSession, install_dir: Path, *, current: str = __version__,
  version_url: str = VERSION_URL, archive_url: str = ARCHIVE_URL)` with `current`, `installable`,
  `async check() -> UpdateInfo`, `async stage() -> str`, `async install() -> str`.
  `install_dir` default for the app: `Path(mediaforge.__file__).resolve().parents[2]`.

Port `UpdateService` from AirGrabber; aiohttp instead of httpx (`Cache-Control: no-cache` on the
version request, 30 s timeout, streamed download capped at 100 MB). File work runs via
`asyncio.to_thread`. The fake GitHub is an in-process aiohttp `TestServer` like
`tests/helpers/fake_tmdb.py`.

- [ ] **Step 1: Write the failing tests:** `test_check_reports_newer_version`,
  `test_check_404_says_no_version_yet`, `test_check_unreachable_raises_update_error`,
  `test_stage_downloads_and_stages`, `test_download_over_limit_is_refused` (limit injectable for
  the test), `test_development_copy_never_stages_or_installs` (`installable is False`, both raise).
- [ ] **Step 2: Run** `PY -m pytest tests/api/test_updates.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: pass.
- [ ] **Step 5: Commit** `feat: update service checks GitHub and stages updates`.

### Task 3: Installed-apps entry (`platform/install_info.py`)

**Files:**
- Create: `src/mediaforge/platform/install_info.py`
- Test: `tests/platform/test_install_info.py`

**Interfaces:**
- Produces: `UNINSTALL_KEY`, `class Registry(Protocol)` (`read(key) -> dict | None`,
  `write(key, values)`), `class WindowsRegistry`,
  `ensure_uninstall_entry(install_dir: Path, version: str, *, registry: Registry | None = None) -> None`,
  `entry_values(root: str, version: str) -> dict[str, str | int]`. Stdlib only.

Port AirGrabber's `install_info.py` with MediaForge's values (DisplayName `MediaForge`,
DisplayIcon `src\mediaforge\ui\assets\MediaForge.ico`, uninstaller
`powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<install>\src\mediaforge\uninstall.ps1"`).
Keep the value names identical to what Task 7's `Register-Uninstaller` writes.

- [ ] **Step 1: Failing tests** with a dict-backed fake registry:
  `test_creates_entry_when_missing`, `test_refreshes_only_display_version`,
  `test_leaves_another_folders_entry_alone`, `test_uninstall_string_quotes_paths_with_spaces`
  (`C:\Users\Jürgen Smit\AppData\Local\Programs\MediaForge`).
- [ ] **Step 2–4:** run `PY -m pytest tests/platform/test_install_info.py -q` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: keep the Installed apps entry up to date`.

### Task 4: Launch-time package check (`platform/bootstrap.py`, `ui/bootstrap_window.py`)

**Files:**
- Create: `src/mediaforge/platform/bootstrap.py`, `src/mediaforge/ui/bootstrap_window.py`
- Test: `tests/platform/test_bootstrap.py`

**Interfaces:**
- Produces: `requirements(pyproject: Path) -> list[Requirement]` (from `[project].dependencies`
  via `tomllib`), `unmet(reqs, installed_version=...) -> list[Requirement]`,
  `pip_commands(reqs) -> list[list[str]]` (`sys.executable -m pip install --disable-pip-version-check
  --no-input ...`, `ensurepip` first when pip is missing),
  `ensure_requirements(pyproject: Path, log_file: Path, *, run_pip=..., window=...) -> bool`.
  `ensure_requirements` takes a named setup mutex `Local\MediaForge-setup-<user>` (ctypes): the
  process that gets it runs pip with the progress window; every other process waits for the mutex
  (up to 10 minutes), re-checks, and continues without running pip.
  `ui/bootstrap_window.py` is plain `tkinter` only (no customtkinter: it may be what's missing).

Port AirGrabber's `bootstrap.py` (lenient version compare, plain-Tk progress window, failure
message pointing at the log). `platform/bootstrap.py` must not import tkinter (layering test); it
receives the window as a callable.

- [ ] **Step 1: Failing tests:** `test_reads_dependencies_from_pyproject` (the real
  `pyproject.toml`), `test_unmet_finds_missing_and_too_old`, `test_nothing_unmet_runs_no_pip`,
  `test_only_one_process_runs_pip` (two threads call `ensure_requirements` with a fake `run_pip`
  that sleeps 0.3 s and counts calls; a test-only mutex name: exactly 1 call, both return True).
- [ ] **Step 2–4:** `PY -m pytest tests/platform/test_bootstrap.py -q` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: install missing packages at launch, one process at a time`.

### Task 5: Launcher, shutdown order and in-app updates

**Files:**
- Create: `main.py` (repo root), `src/mediaforge/launch.py`
- Modify: `src/mediaforge/__main__.py` (delegate to `launch.main`),
  `src/mediaforge/platform/single_instance.py` (split `Primary.close()` into
  `stop_listening()` + `close()`), `src/mediaforge/app.py` (`Services.updates`, `run()` returns
  `RunOutcome(restart: bool)`, `run()` calls `primary.stop_listening()` not `close()`, update
  check task, `_legacy_dirs` loses its frozen branch), `src/mediaforge/db/settings.py`
  (`auto_update: bool = True`), `src/mediaforge/ui/main_window.py` (update note),
  `src/mediaforge/ui/settings_view.py` (About: version, Check for updates / Update to vX,
  Automatic updates switch)
- Test: `tests/test_launch.py`, `tests/ui/test_settings_view.py`, `tests/ui/test_shell.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `launch.main(argv: Sequence[str], *, install_dir: Path, hooks: LaunchHooks | None = None) -> int`
  where `LaunchHooks` (dataclass) injects `ensure_requirements`, `try_become_primary`,
  `send_to_primary`, `run_app`, `apply_staged`, `ensure_uninstall_entry`, `relaunch` for tests.
  `main.py` (stdlib only): put `<here>\src` first on `sys.path`, then
  `sys.exit(launch.main(sys.argv[1:], install_dir=<here>))`.
  `launch.LAUNCHER: Path | None` — set when started through `main.py` (Task 6 reads it).

Deliberate difference from the spec, to be ledgered as a ruling: the spec has secondary
processes skip the package check, but MediaForge's hand-over path (`platform/cli.py`,
`single_instance.InstanceMessage`) already needs pydantic, so a missing package would crash a
right-click launch. Instead every process runs the check, which is a few milliseconds when
nothing is missing, and the setup mutex makes sure only one of them ever runs pip.

Order inside `launch.main`: `ensure_requirements` (stdlib only, nothing third-party imported
before it) → parse args with `platform/cli.py` → `--self-test` / `--install-menu` / `--remove-menu`
actions (Task 6) → `try_become_primary`; secondary: `send_to_primary` and return → primary:
`asyncio.run(run(...))` → then, still holding the mutex: if installed copy, `apply_staged`,
`ensure_uninstall_entry` on success; `primary.close()`; `relaunch()` when the outcome asks for it.

In the app: one check ~10 s after start in both the main window and confirm-only mode; with
`auto_update` it stages and shows "Restart to update" (main window) / logs it; otherwise shows
"Update to vX" which opens Settings. Settings' **Update to vX** downloads and installs, then
restarts through the same path as closing (`MainWindow.close` → `QueueView.request_close` waits
for a running Forge/Undo), with `restart=True`.

- [ ] **Step 1: Failing tests** (`tests/test_launch.py`, all with fake hooks):
  `test_secondary_hands_over_and_never_applies_updates`,
  `test_primary_applies_staged_update_before_releasing_the_lock` (record call order:
  `run_app`, `apply_staged`, `ensure_uninstall_entry`, `close`),
  `test_development_copy_never_applies`, `test_restart_outcome_relaunches_after_release`,
  `test_main_py_imports_nothing_third_party` (run `main.py --help`-free import in a subprocess with
  `-X importtime` or a `sys.modules` snapshot: no `pydantic`, `aiohttp`, `customtkinter` before
  `ensure_requirements` is called).
  UI: `test_settings_shows_version_and_offers_update` (fake UpdateService says 1.2.0 available),
  `test_update_now_waits_for_a_running_forge` (busy queue view: restart only after the run ends).
- [ ] **Step 2: Run** `PY -m pytest tests/test_launch.py tests/ui/test_settings_view.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite `PY -m pytest -q -p no:cacheprovider` — Expected: all pass.
- [ ] **Step 5: Commit** `feat: launcher applies updates after shutdown; update check in the app`.

### Task 6: Right-click menu for installed copies

**Files:**
- Modify: `src/mediaforge/platform/context_menu.py`, `src/mediaforge/platform/cli.py`,
  `src/mediaforge/launch.py`, `src/mediaforge/app.py`, `src/mediaforge/ui/settings_view.py`,
  `src/mediaforge/ui/main_window.py`
- Test: `tests/platform/test_context_menu.py`, `tests/ui/test_settings_view.py`

**Interfaces:**
- Consumes: `launch.LAUNCHER`.
- Produces: `command_prefix(launcher: Path | None = None) -> list[str]` — with a launcher:
  `[<pythonw.exe next to sys.executable>, str(launcher)]`; without: `[pythonw, "-m", "mediaforge"]`
  (frozen branch removed). `menu_presets(all_names: Sequence[str], chosen: Sequence[str]) -> list[str]`
  (`chosen` empty = all; names no longer present dropped). `async def refresh_menu(services)` in
  `app.py`: when `installed_command()` is not `None`, reinstall entries for `menu_presets(...)`.
  CLI flags `--install-menu` and `--remove-menu`: open the database (`MEDIAFORGE_HOME` honoured),
  seed default presets if empty, install for all presets with `context_menu_presets = []`, exit 0;
  no window.

Refresh runs at every start and from `SettingsView.on_presets_changed`. In Settings, ticking every
preset stores `[]` (= all, so presets added later appear).

- [ ] **Step 1: Failing tests:** `test_command_prefix_uses_the_launcher`,
  `test_menu_command_quotes_non_ascii_paths` (`C:\Users\Jürgen Smit\...\main.py`),
  `test_menu_presets_empty_means_all_and_drops_deleted`,
  `test_all_ticked_saves_empty_list`, `test_install_menu_flag_installs_all_presets` (fake
  `context_menu.install`, temp `MEDIAFORGE_HOME`).
- [ ] **Step 2–4:** `PY -m pytest tests/platform/test_context_menu.py tests/ui/test_settings_view.py -q`
  FAIL → implement → PASS; then the full suite.
- [ ] **Step 5: Commit** `feat: right-click menu follows the installed copy and new presets`.

### Task 7: `install.ps1` and `uninstall.ps1`

**Files:**
- Create: `install.ps1` (repo root), `src/mediaforge/uninstall.ps1`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: Task 3's value names, Task 6's `--install-menu` / `--remove-menu`, `--self-test`.
- Produces: `install.ps1` parameters `-InstallDir`, `-Archive`, `-NoShortcuts`, `-NoLaunch`,
  `-NoMenu`, `-UninstallKey` (default the real key; tests pass a scratch key under
  `HKCU:\Software\MediaForge-test`); `uninstall.ps1` parameters `-Quiet`, `-UninstallKey`.

`install.ps1` steps (spec "install.ps1"): winget → Python 3.13+ (ported `Find-Python`/`Test-Python`,
which rejects the Store stub) → download/`-Archive`, validate, copy app entries → create or reuse
`.venv` with that Python, `.venv\Scripts\python.exe -m pip install` the `[project].dependencies`
(read from `pyproject.toml` by the venv's Python with `tomllib`) → `.venv\Scripts\python.exe main.py --self-test`
(stop with a plain message on failure) → shortcuts (`.venv\Scripts\pythonw.exe`, `"<install>\main.py"`,
icon) → `Register-Uninstaller` → `main.py --install-menu` unless `-NoMenu` → start unless `-NoLaunch`.
`uninstall.ps1`: port; asks the user to close MediaForge if its mutex exists; runs
`main.py --remove-menu` before deleting files; data question default No; refuses a folder without
`main.py` or with `tests\`.

- [ ] **Step 1: Failing tests** (always run): `test_scripts_parse_in_windows_powershell_51`
  (`powershell.exe -NoProfile -Command` with `[System.Management.Automation.Language.Parser]::ParseFile`
  → no errors for both), `test_scripts_are_ascii_and_avoid_51_traps` (no `&&`, `??`, `?:`-style
  ternary, no line starting with `exit`). Opt-in (`MEDIAFORGE_INSTALLER_TESTS=1`, needs internet for
  pip): `test_install_from_local_archive_then_quiet_uninstall` — zip the working tree's app entries
  into a GitHub-shaped archive, install into `tmp_path / "Programs dir with spaces"` with
  `-NoShortcuts -NoLaunch -NoMenu -UninstallKey <scratch>`; assert entries + `.venv` exist and the
  scratch key has `DisplayVersion` = current version; then `uninstall.ps1 -Quiet -UninstallKey <scratch>`
  removes the folder and the key; `MEDIAFORGE_HOME` points at a temp dir throughout.
- [ ] **Step 2: Run** `PY -m pytest tests/test_scripts.py -q` — Expected: FAIL (scripts missing).
- [ ] **Step 3: Implement** both scripts by porting.
- [ ] **Step 4: Run** it, then once with `MEDIAFORGE_INSTALLER_TESTS=1` — Expected: pass.
- [ ] **Step 5: Commit** `feat: one-line installer and uninstaller`.

### Task 8: Retire the PyInstaller build; repository ready to publish

**Files:**
- Delete: `release/` (all five files), `tests/test_release.py`
- Create: `README.md`, `LICENSE` (MIT, `Copyright (c) 2026 drunkgummyboy`),
  `tests/test_no_secrets.py`, `docs/superpowers/verification/2026-10-31-install-checklist.md`
- Modify: `pyproject.toml` (drop `pyinstaller`; marker `installer`), `src/mediaforge/__init__.py`
  (`1.1.0`), `CLAUDE.md` (Status, Dev setup, Build → Release; NAS name → `\\NAS\Multimedia\...`),
  `docs/superpowers/verification/2026-10-26-legacy-parity.md` (packaged-program row, NAS name),
  `tests/test_legacy_parity.py` if it references release tests, any remaining `sys.frozen` code.

README: modelled on AirGrabber's — Install (`irm is.gd/mediaforge | iex`, plus the full raw URL),
Update, Uninstall (the data question), "If MediaForge stops starting, paste the install line
again", "MediaForge 1.x single file" note for `MediaForge.py`, Development commands. Checklist:
the owner-only checks from the spec's Testing section, in plain steps.

- [ ] **Step 1: Failing tests:** `test_no_tracked_file_contains_the_tmdb_key` (reads the local
  `mediaforge_config.json` if present, scans `git ls-files` contents, never prints the key; skips
  when no local key), `test_config_and_undo_files_are_never_tracked`, and
  `test_nothing_frozen_left` (no `sys.frozen` / `PyInstaller` in `src`).
- [ ] **Step 2: Run** `PY -m pytest tests/test_no_secrets.py -q` — Expected: `test_nothing_frozen_left` FAILS.
- [ ] **Step 3: Implement** the deletions and documents.
- [ ] **Step 4: Run** the full suite, `ruff check src tests`, `ruff format --check src tests`,
  pyright with `--pythonpath` — Expected: all clean.
- [ ] **Step 5: Commit** `chore: run from source; retire the PyInstaller build; README and licence`.

### Task 9: Publish (owner's OK required at this step)

No code. Stop and ask the owner before Step 3; describe exactly what will happen.

- [ ] **Step 1:** Rewrite the author and committer address of every commit to
  `beau_spiessens@hotmail.com` (`git filter-branch --env-filter`), keeping messages and dates;
  verify `git log --format='%ae %ce' | sort -u` shows only that address (plus none other).
- [ ] **Step 2:** Re-run `tests/test_no_secrets.py` on the rewritten history (scan every commit
  with `git grep` for the key; count only, never print).
- [ ] **Step 3 (after the owner's yes):** add remote `https://github.com/drunkgummyboy/Media-Forge.git`,
  `git push --force origin main`.
- [ ] **Step 4:** Verify from the outside: the raw `install.ps1` URL and the version URL answer
  200 with `1.1.0`; `mediaforge_config.json` is gone from the page.
- [ ] **Step 5:** Tell the owner, in plain words, to make the short link `is.gd/mediaforge` → the
  raw `install.ps1` URL and to run through the install checklist.
