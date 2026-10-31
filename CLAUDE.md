# MediaForge

Windows desktop tool that renames messy release-scene video filenames into clean,
standardized names and folders, using TMDB for official titles, years, episode names
and posters, and yt-dlp for trailers.

## Status

The rewrite (1.x) is done and installs and updates itself from the public page
github.com/drunkgummyboy/Media-Forge, like the owner's AirGrabber. The legacy app is the
single file `MediaForge.py` (older copies: `MediaForge_v0_4.py`, `MediaForge_v0_5.py`).
Treat it as the behavioural reference: the rewrite keeps everything it does. Don't edit
the legacy files.

Design (from the owner's blueprint): async, modular, strongly typed.

- `src/mediaforge/ui/` CustomTkinter + tkinterdnd2 views and dialogs, bridged to asyncio
  with async-tkinter-loop
- `src/mediaforge/core/` queue manager, filename parser, renaming/format engine, cleanup
- `src/mediaforge/api/` aiohttp TMDB client, yt-dlp subprocess wrapper
- `src/mediaforge/db/` aiosqlite: settings, presets, TMDB cache, undo history
- `src/mediaforge/models/` Pydantic models
- Standard `logging` with a handler that forwards records to the UI log box
- Dependencies declared in `pyproject.toml`; installed copies run from source in their own
  `.venv`, and the launcher installs missing packages before anything imports them
- yt-dlp/ffmpeg are fetched on first use and checked against their published checksums

## Dev setup

- Design: `docs/superpowers/specs/2026-10-25-mediaforge-rewrite-design.md`, and for
  installing and updating `docs/superpowers/specs/2026-10-31-install-and-update-design.md`
- Plans: `docs/superpowers/plans/`
- The venv lives outside Google Drive: `%LOCALAPPDATA%\MediaForge-dev\venv`, with the
  project installed editable (`pip install -e ".[dev]"`). Call it as
  `"$LOCALAPPDATA/MediaForge-dev/venv/Scripts/python.exe"` (written `PY` below).
- Tests: `PY -m pytest` (UI tests are marked `ui`). Lint: `PY -m ruff check src tests`
  and `PY -m ruff format src tests`. Types: `PY -m pyright --pythonpath <PY as a
  Windows path>` (without `--pythonpath`, pyright picks the global Python and can't
  find the dependencies).
- Tests always run with `MEDIAFORGE_HOME` pointing at a temp dir (autouse fixture).
  Use the same variable for manual runs so the real app data stays untouched.

## Install, update and release

- Users install with `irm is.gd/mediaforge | iex` (`install.ps1`): Python 3.13 if missing,
  the app entries `main.py`, `pyproject.toml` and `src/` copied into
  `%LOCALAPPDATA%\Programs\MediaForge`, packages in its `.venv`, self-test, shortcuts,
  Installed-apps entry, right-click menu. `src/mediaforge/uninstall.ps1` undoes it.
- `main.py` (stdlib only) → `mediaforge/launch.py`: package check, hand-over or app, then a
  staged update is swapped in while the single-instance lock is still held.
- Updates: `api/updates.py` checks `src/mediaforge/__init__.py` on GitHub `main` and stages
  the branch zip in `<install>\.update`; `platform/update_files.py` swaps it in. Only a
  higher `__version__` is installed; a folder with `tests/` or `.git` never updates.
- Release: bump `__version__` in `src/mediaforge/__init__.py`, run the tests, commit, push
  `main`. Every installed copy picks it up on its next start. `install.ps1` and
  `uninstall.ps1` must stay Windows PowerShell 5.1-safe (no `&&`, `??`, ternaries, `exit`;
  ASCII); `tests/test_scripts.py` checks that, and `MEDIAFORGE_INSTALLER_TESTS=1` runs a
  real install into a temp folder.
- Commits pushed to GitHub use the author address `beau_spiessens@hotmail.com`
  (this repo's `user.email`), never the owner's Gmail address.

## Things learned the hard way

- Tk on Windows: never mark a pop-up `transient()` (it crashed Tk once destroyed
  before being shown); use `ui/widgets/popups.prepare_popup`.
- `ui/gc_guard` keeps Python's cycle collector on the Tk thread; don't re-enable
  automatic GC.
- Don't run CPU-heavy Python (guessit) in worker threads: it starves the Tk thread
  of the GIL. Parse on the event loop in chunks (`core/queue.py`).
- UI tests share one Tk root (`tests/ui/conftest.py`); creating many roots in one
  process fails at random.
- Tests never touch the real right-click menu (autouse `no_real_menu` in
  `tests/conftest.py`) or the real Installed-apps entry (fakes, or a scratch key).

## Owner's open to-do items (ToDo.txt)

- Right-click (Explorer context menu) runs should open a styled window listing each
  file's old and new name; it must handle many files at once.
- Undo any of the last 50 runs, not only the latest, and not necessarily in order.

## Things to know

- Windows only. Python 3.13. Files often live on a NAS share (UNC paths like
  `\\NAS\Multimedia\...`), so never assume a drive letter.
- `mediaforge_config.json` holds the user's TMDB API key and presets. It is
  git-ignored; never commit it, print the key, or paste it into chat. The rewrite
  should import it (and `mediaforge_undo.json`) once into the new database.
- Format variables: `{ny}` name + year, `{t}` title, `{s}` season, `{e}` episode,
  `{s00e00}` e.g. S01E02 (or S01E02-E03), `{en}` episode name. `/` makes folders.
- The project folder is synced by Google Drive.
- The owner is not a programmer: explain results in plain language.
