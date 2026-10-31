# MediaForge: install, update and uninstall like AirGrabber

Date: 2026-10-31. Status: draft for the owner's approval.

## Intent

The owner wants MediaForge to install, update and uninstall exactly the way his AirGrabber
program does (the AirGrabber project folder, published at
github.com/drunkgummyboy/AirGrabber). Today MediaForge 1.0.0 is a zip with a PyInstaller-built
`MediaForge.exe` that you unzip anywhere. That has three problems for "me and others":

- the Explorer right-click menu remembers the folder the program was in, so moving the folder
  breaks it;
- there's no Start menu entry and no entry under Windows Settings › Apps;
- nobody gets new versions unless they download a new zip by hand.

A fixed install location is also the groundwork for a later project, a folder watcher that starts
with Windows. That watcher is **not** part of this design.

## What the owner decided

1. The install, update and uninstall procedure is the same as AirGrabber's: the README's Install,
   Update and Uninstall sections describe what the person installing it sees.
2. MediaForge does not have to stay a ready-made program. "The Python way" is fine: the installer
   adds Python if needed and MediaForge runs from its source files, like AirGrabber.
3. MediaForge's code is public on GitHub.
4. The new version **replaces everything** on the existing public page
   github.com/drunkgummyboy/Media-Forge (the old single-file MediaForge lives there now).
5. The owner's TMDB key must never be made public. (That page's `mediaforge_config.json`
   held his key. On 2026-10-31 he generated a new key, so the exposed one no longer works;
   the new key is entered in MediaForge's own settings, never in chat or in a file in the
   repository.)
6. The owner accepted this outline ("proceed"):
   - Install: one line pasted into PowerShell. It adds Python when missing, downloads
     MediaForge from the Media-Forge page into the user's Programs folder, gives it its own
     private package folder, adds Start menu and desktop shortcuts, lists it under Settings ›
     Apps and opens it. Running the line again reinstalls the latest version.
   - The short link `is.gd/mediaforge` (like `is.gd/airgrabber`) is made by the owner.
   - Settings and history stay where they are now; installing and updating never touch them.
   - First start: the owner's TMDB key and old settings come over automatically (the existing
     legacy import). Other people get the existing welcome window asking for their own free key.
   - The installer turns the right-click menu on.
   - Updates as in AirGrabber: checked at start, downloaded in the background, installed when
     MediaForge closes; Settings › Check for updates installs at once and restarts. The
     development folder never updates itself.
   - Uninstall from Settings › Apps removes the right-click menu and asks whether to also
     delete settings and history.
   - On GitHub the new version replaces the old one under the same MIT licence; the two icon
     files stay at the top level so old copies keep their icon. Nothing goes online without
     the owner's OK at that moment.
   - The zip with the ready-made program goes away.
7. Published changes show the owner's Hotmail address (`beau_spiessens@hotmail.com`), the one
   his AirGrabber and old Media-Forge pages already show, not his Gmail address. Before
   publishing, the author address of all existing saved versions is switched to it, and this
   repository's git settings use it from then on.

## Goals

- A new user on Windows 10/11 installs MediaForge by pasting one PowerShell line, without
  administrator permission, and without Python installed beforehand.
- Installed copies update themselves from github.com/drunkgummyboy/Media-Forge.
- Uninstalling leaves nothing behind except, when the user says so, their settings and history.
- The right-click menu always points at the installed copy.

## Non-goals

- The folder watcher, a tray icon, start-with-Windows (the next project).
- Code signing, a classic setup.exe, the Microsoft Store, winget publishing.
- A Dutch translation (MediaForge is English-only).
- qBittorrent, Prowlarr and the other AirGrabber-specific installer parts.
- Removing the legacy app's HKLM right-click entries during install (Settings already offers the
  removal `.reg`).

## Anti-goals (a failure even if it technically works)

- The TMDB key, `mediaforge_config.json` or `mediaforge_undo.json` reaching GitHub.
- An update that installs while a rename is running, or that loses or corrupts History.
- An installed copy "updating" to an older or equal version, or a half-copied install that won't
  start (an interrupted update must leave the old version working).
- The development folder (Google Drive) updating or uninstalling itself.
- Installing that needs administrator permission or changes anything outside the current user.
- After uninstall, a right-click menu entry that points at a program that no longer exists.
- Install or update messages a non-programmer can't follow.

## Constraints

- Windows 10/11, current user only (HKCU, `%LOCALAPPDATA%`, no UAC prompt).
- `install.ps1` and `uninstall.ps1` run under Windows PowerShell 5.1, `install.ps1` under
  `irm … | iex`: no `&&`, ternaries, `??`, and no `exit`; ASCII only. These are AirGrabber's rules.
- Python 3.13 or newer (`requires-python` in `pyproject.toml`).
- Data folders stay as they are: `%APPDATA%\MediaForge` (database) and
  `%LOCALAPPDATA%\MediaForge` (logs, cache, trailer tools, instance key). `MEDIAFORGE_HOME`
  still overrides both for development and tests.
- The single-instance hand-over must stay fast: Explorer starts one process per selected file,
  and those secondary processes may import only light modules (see `src/mediaforge/__main__.py`).
- Everything else in `CLAUDE.md` still holds (layering, one asyncio loop, `gc_guard`, parsing on
  the loop, `prepare_popup`, long paths, the key never logged or printed).

## How it works

AirGrabber's files are the model. Port them, keeping their behaviour and their hard-won details
(antivirus rename retries, `.part`-style staging, read-only clean-up, winget fallbacks, failure
dialogs), and change only what MediaForge needs.

### The installed copy

`%LOCALAPPDATA%\Programs\MediaForge\` holds exactly the app entries
`main.py`, `pyproject.toml` and `src\` (the `mediaforge` package, assets included), plus:

- `.venv\` — MediaForge's private Python environment, created by the installer with the Python
  3.13 it found or installed. All packages from `[project].dependencies` go in here, never into
  the user's own Python. Updates and reinstalls leave it in place; the launch-time package check
  keeps it in step.
- `.update\` — exists only while an update is staged.

Updates and reinstalls replace exactly the app entries and nothing else.

### The launcher: `main.py` (new, at the repository root)

The entry point for installed copies, shortcuts and the right-click menu, started as
`.venv\Scripts\pythonw.exe <install>\main.py [args]`. It also works from a checkout.
Its top-level imports are standard-library only. It:

1. puts `<its folder>\src` first on `sys.path`;
2. runs the existing light start-up path (argument parsing, `try_become_primary` /
   `send_to_primary`). A secondary process hands its files over and exits **without** running the
   package check, so right-click hand-overs stay fast;
3. in the primary only: runs the package check (port of AirGrabber's `app/bootstrap.py`: compare
   `pyproject.toml`'s dependencies with what's installed, run pip in the venv with a small
   progress window only when something is missing or too old; read `pyproject.toml` with
   `tomllib`);
4. runs the app (`asyncio.run(run(...))` as today);
5. after the app has shut down and **before** the single-instance lock is released: applies a
   staged update (port of `apply_staged`), refreshes `DisplayVersion` in the Installed-apps entry,
   and relaunches itself when the user chose "update now".

`python -m mediaforge` keeps working for development.

### Updates (port of AirGrabber's `app/services/updates.py`, as `src/mediaforge/api/updates.py`)

- Version: `__version__` in `src/mediaforge/__init__.py`, read from
  `https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/main/src/mediaforge/__init__.py`.
- Archive: `https://codeload.github.com/drunkgummyboy/Media-Forge/zip/refs/heads/main`, max 100 MB,
  validated (one top folder, every app entry present, its version newer) before anything changes.
- Only a strictly higher version is offered. A copy whose folder has a `tests\` folder is a
  development copy: it can check, but never stages, installs or applies.
- Schedule and wording as AirGrabber's main window: one check shortly after start; with
  automatic updates on (new setting, default on) the update is staged in the background and a
  "Restart to update" note appears; otherwise an "Update to vX" note opens Settings.
- Settings gets the version number and **Check for updates** / **Update to vX**, which installs at
  once and restarts. If a run (Forge or Undo) is going, the restart waits for it, the same way
  closing does today (`QueueView.request_close`).
- HTTP uses aiohttp (MediaForge's client), not httpx. Never log full URLs with secrets (none
  here, but keep the habit).

### `install.ps1` (port of AirGrabber's, at the repository root)

Run as `irm is.gd/mediaforge | iex`, or with the full raw URL. Steps, in order:

1. set up winget if missing (AirGrabber's `Initialize-Winget`, never fatal);
2. find Python 3.13+, or install it per-user (winget, else python.org);
3. download the archive (or `-Archive <zip>` for testing), check it, copy the app entries into
   `-InstallDir` (default `%LOCALAPPDATA%\Programs\MediaForge`);
4. create or reuse `.venv`, install the dependencies quietly;
5. run `main.py --self-test` once (the existing self-test) and stop with a plain message if it
   fails;
6. Start menu and desktop shortcuts (`pythonw.exe` from `.venv`, `main.py`, MediaForge's icon);
   `-NoShortcuts` skips them;
7. register the Installed-apps entry under
   `HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaForge`
   (publisher drunkgummyboy, URL the GitHub page, uninstaller `src\mediaforge\uninstall.ps1`);
8. turn the right-click menu on for this install (through `main.py`, using the existing
   `platform/context_menu.py`, whose launch command becomes the venv's `pythonw.exe` plus
   `main.py`);
9. start MediaForge (`-NoLaunch` skips it).

Failures show the plain message in the console and in a small window, like AirGrabber's.

### `uninstall.ps1` (port of AirGrabber's)

Removes the install folder, this install's shortcuts, the right-click menu entries (HKCU) and the
Installed-apps entry. Then it asks "Also delete your settings and history?" (default **No**); Yes
deletes `%APPDATA%\MediaForge` and `%LOCALAPPDATA%\MediaForge`. `-Quiet` asks nothing and keeps
the data. It refuses to remove a folder without `main.py` or with a `tests\` folder. If MediaForge
is running, it asks the user to close it first.

### The repository and publishing

- Add at the root: `README.md` with plain Install / Update / Uninstall sections modelled on
  AirGrabber's README, `LICENSE` (MIT, "Copyright (c) 2026 drunkgummyboy", as the old page has),
  `install.ps1`, `main.py`. Keep `MediaForge.png` and `MediaForge.ico` at the root.
- Remove the PyInstaller build: `release/`, `tests/test_release.py`, the `pyinstaller` dev
  dependency and the frozen-only code paths (`sys.frozen` branches). Keep `--self-test`.
- Update `CLAUDE.md` (status, build and release sections) and the release procedure: bump
  `__version__`, run the tests, push `main`.
- Before every push, a check that no tracked file contains the TMDB key or a config/undo file
  (a small script or test that reads the local key without printing it).
- **Publishing**: push this repository's history to github.com/drunkgummyboy/Media-Forge `main`,
  replacing the page's old history (which also removes the old `mediaforge_config.json` from
  the page). This is a force push to a public page: ask the owner right before doing it.
- The first published version is **1.1.0**.

## Testing

- Unit tests with temporary folders and the in-process aiohttp test server (no live GitHub):
  version compare, archive validation, stage, apply (including an interrupted swap leaving the old
  version), development-copy refusal, the package check's requirement parsing.
- The launcher: secondary processes never run the package check or an update.
- `install.ps1` against a local `-Archive` zip into a temporary `-InstallDir` with `-NoShortcuts
  -NoLaunch`, and `uninstall.ps1 -Quiet` against that folder, in a way that never touches the
  owner's real install, shortcuts or right-click menu (parameters or environment overrides for the
  registry keys and folders, as the builder sees fit).
- Owner-only checks, written up as a short checklist: a real install on a PC or virtual machine
  without Python, an update from 1.1.0 to a test 1.1.1, the right-click menu after install, and
  an uninstall answering No and then Yes.

## Calls Claude made (for the owner to check)

- Packages live in a private `.venv` inside the install folder, not in the user's own Python
  (AirGrabber uses the user's Python). Reason: no clashes with AirGrabber or anything else.
- Automatic updates are on by default, with a setting to turn them off.
- Right-click (confirm-only) launches also check for updates, so someone who only ever
  right-clicks still gets them; an update staged there installs when that window closes.
- When the right-click menu is on, MediaForge refreshes its entries at every start and whenever
  presets change, so presets imported on first start appear in the menu.
- The old single-file `MediaForge.py` (and its `_v0_4` / `_v0_5` copies) stay in the repository
  as the behavioural reference; the README says so, as AirGrabber's README does for
  `airgrabber.py`.
- Before publishing, the NAS name in `CLAUDE.md` and the docs is replaced with a generic example
  (`\\NAS\Multimedia\...`).
- The README's help section says: if MediaForge stops starting (for example after Python was
  removed), paste the install line again.
- `install.ps1` runs MediaForge's self-test before it adds shortcuts.

## Left to the builder

- Exact module names and the split between `main.py` and `src/mediaforge/platform/`.
- Whether the package-check window reuses AirGrabber's plain-Tk window or a simpler one.
- How install/uninstall tests isolate the registry and folders.
- Wording of messages, within "plain language for a non-programmer".
