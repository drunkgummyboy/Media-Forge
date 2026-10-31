# MediaForge rewrite: design

Date: 2026-10-25
Status: draft for owner approval
Behavioural reference: `MediaForge.py` (legacy, ~3,000 lines, single file)

## 1. Intent

MediaForge turns messy release-scene video filenames into clean, standardized names
and folders. It reads the title, year, season and episode from the filename, looks up
the official data on TMDB, then renames and moves the file using a user-defined
format. Optionally it adds posters and a trailer, and tidies up junk.

The owner's verdict on the current app: **"nothing big, just modernize."** It works.
The rewrite is about making it:

- **sturdy**: it never freezes, never loses a file, and keeps settings and history
  safe across crashes;
- **shareable**: other people will use it (through the owner's GitHub page), so it
  must run as a standalone Windows program with no Python install and have a
  friendly first start;
- **easy to keep improving**: small, separate, tested parts instead of one file.

The rewrite also delivers the owner's two open to-do items (from `ToDo.txt`) and a
fresh visual design.

## 2. Goals

1. **Keep every capability of the legacy app.** Appendix A lists them all. Nothing
   on that list may be dropped without asking the owner.
2. **Confirm window for right-click renames.** Renaming from the Explorer context
   menu opens one window listing every selected file's new name over its old name,
   each with a tick box. Nothing changes on disk until the user clicks **Rename N
   files**. It must handle many selected files at once and show them in one window,
   not one window per file.
3. **History with selective undo.** Keep the last 50 runs. Any run can be undone, in
   any order. The user can undo a whole run or open it and tick only the files to
   put back (renames and downloaded posters or trailers alike).
4. **The "Studio" look.** The owner picked this design from three mockups (§6).
5. **Responsive.** The window never freezes, whether TMDB lookups, poster
   downloads, trailer downloads or scanning a folder of 1,000+ files on a NAS is
   running.
6. **Standalone and shareable.** A Windows download that runs without Python, with
   a first-start setup for new users.
7. **Carry the owner's data over.** On first start, import the existing presets,
   TMDB key and last-run undo information automatically.

## 3. Non-goals

- macOS or Linux support. This is a Windows app.
- Metadata sources other than TMDB (TVDB, OMDb, and so on).
- Handling files other than videos and MediaForge's own artwork. Subtitles, NFO
  files and so on are not moved or renamed. This is a candidate for a future version.
- An installer (MSI or setup.exe) for v1. A zipped folder with `MediaForge.exe` is
  enough. Section 9 keeps the door open.
- Auto-updating MediaForge itself.

## 4. Anti-goals (failure even if it "works")

- A user's video gets overwritten, lost, or renamed without them being able to undo
  it.
- The right-click flow renames anything before the user confirms.
- The window hangs or shows "Not responding".
- The app needs Python, pip, or an internet download of Python packages at run time.
  The legacy `_auto_install_deps()` must not come back in any form.
- The TMDB API key ends up in logs, crash reports, the git repo or the build.
- Undo deletes a file the user put there themselves.

## 5. Decided by the owner

| Topic | Decision |
|---|---|
| Scope | Modernize. Keep all features. No big behaviour changes beyond the items below. |
| Right-click flow | Preview first, then the user confirms. Files can be unticked. |
| Undo | Whole run **or** chosen files, for any of the last 50 runs, in any order. |
| Look | "Studio" (mockup A), plus the History and Confirm screens as mocked up. |
| Audience | The owner **and other people**, so first-start setup and a proper download matter. |
| Tech stack | As in the owner's blueprint: Python 3.13, CustomTkinter + tkinterdnd2 bridged to asyncio with async-tkinter-loop, aiohttp, Pydantic v2, aiosqlite, standard `logging`, dependencies in `pyproject.toml`, PyInstaller build. |
| Layout | As in the blueprint: `ui/`, `core/`, `api/`, `db/`, `models/`. |

## 6. The Studio design

The mockups live in `.superpowers/brainstorm/543-1792933811/content/`
(`main-window-looks.html`, option A, and `history-and-confirm.html`). The look is dark
and calm, with one warm amber accent (`#f2a33a`) on a near-black base (`#16181d` main,
`#111317` rail and title bar, `#1e2128` and `#262a33` surfaces, `#e8eaed` text,
`#8b93a1` muted, `#4cc38a` success). It also has a light variant with the same layout.
The theme setting is Dark, Light, or System (follows Windows).

### 6.1 Main window

- **Left icon rail** (about 52 px): logo, then **Queue**, **History**, **Settings**.
  The active view is highlighted in amber.
- **Queue view** (centre):
  - Top bar: the title "Queue", the file count, a preset dropdown, and the format
    string field in a monospace font. Editing the format updates every row live.
  - Adding files: drag and drop anywhere on the window. There are also "Add folder"
    and "Add files" actions; placement is the builder's call, but they must be
    discoverable when the queue is empty. The empty state explains drag and drop.
  - The list is grouped by destination (series and season, folder, or "flat"), as in
    the legacy app. Each row has a small poster thumbnail, the **new name** in bold
    with the `SxxEyy` part in amber, the **old name** beneath it in a muted monospace
    font, and a status mark: matched, needs a choice (`?`), working, done, or error.
    Each row also has a remove (✖) action.
  - Clicking a row selects it and fills the detail panel. Up and down arrow keys move
    the selection, replacing the legacy ◀ ▶ stepper.
  - Bottom bar: option chips (Movie poster, Series poster, Season posters, Trailer,
    Clean junk, Remove empty folders, Scan subfolders), then **Preview** (a dry run)
    and **Forge N files**, the main action button in amber. Choosing a preset sets
    the first six chips. **Scan subfolders** is a separate, remembered setting
    (default on) that presets don't change. The legacy app's preset switch quietly
    turned it off, which looks like a bug; my call. An imported `chk_recursive`
    flag is ignored.
  - A slim **progress** indicator and a collapsible **Activity** log strip. The log
    shows coloured lines (error red, warning amber, success green), fed by the
    logging handler (§8.7).
- **Detail panel** (right, about 250 px): a large poster, the official title and
  year, the episode code and name, "Matched on TMDB · Change match…", the full
  destination path, the original filename, and a "Watch trailer" link that opens a
  YouTube search in the browser, as in the legacy app.

### 6.2 Matches TMDB isn't sure about

The legacy app stops in the middle of a run and pops up a picker dialog. The new app
moves that decision **before** the run:

- When files are added, lookups run in the background. A file whose match isn't
  confident gets a `?` mark and the top result as a provisional choice.
- "Change match…" (detail panel) opens the **match picker**: up to 8 candidates
  with w92 thumbnails, title (year) and a short overview. The actions are "Use this",
  "Skip TMDB for this file" (use parsed name only) and "Use top result".
- Pressing **Forge** while any `?` remain shows one prompt: "N files have uncertain
  matches: Review them / Use top results / Cancel". "Review" steps through the
  pickers in turn.
- A choice applies to every queued file with the same parsed title, year and type,
  as the legacy app does within a run. It is also **remembered in the database**, so
  the same show doesn't need picking again next week (my call; it can be cleared in
  Settings).

### 6.3 History view

As mocked up: the last 50 runs, newest first. Each run shows the date and time, file
count, preset, how it was started (app or right-click) and what was added (posters,
trailer).

- **Undo run** undoes everything in that run that can still be undone.
- **Show files** expands the run into its operations, with tick boxes. **Undo N
  selected** undoes only those.
- A fully undone run is greyed out with "Undone <date>". A partly undone run says so,
  and its remaining items stay undoable.
- Runs can be undone in any order. Before acting, each operation is checked. If it
  can't be safely reversed (see §8.4), it is skipped and the reason is shown in the
  log and the result summary.
- The 51st run pushes the oldest out of history.
- `Ctrl+Z` in the Queue view asks to undo the most recent not-yet-undone run.

### 6.4 Confirm window (right-click flow)

A smaller standalone window in the same style, titled "MediaForge · Confirm rename":

- The header reads "Rename N files?", with a preset dropdown (pre-set to the preset
  chosen in the context menu, and changeable) and the hint "Nothing changes until
  you click Rename".
- Each row has a tick box, the new name over the old name, and a status. Uncertain
  matches are highlighted, with an inline note such as "2 shows match. Using The
  Office (US, 2005) · Change…" that opens the same match picker.
- The footer shows "x of N selected" and three buttons: **Open in MediaForge**
  (moves these files into the full window's queue), **Cancel**, and **Rename x
  files**.
- After renaming, the window shows a short result ("4 renamed, 1 skipped") with
  **Undo** and **Close**. The run lands in History marked "right-click".
- Posters, trailers, cleanup and pruning follow the chosen preset's options, exactly
  as in the main window.

### 6.5 Settings view

Sections: **Appearance** (theme), **TMDB** (API key with validation, and result
language, default `en-US`), **Presets** (create, edit, rename, delete; the last
preset can't be deleted; fields are name, type auto/movie/tv, format, options),
**Right-click menu** (§8.8), **Remembered matches** (list and clear), and **About**
(version, log folder link).

### 6.6 First start

If no database exists yet:

1. If a legacy `mediaforge_config.json` (and `mediaforge_undo.json`) is found next to
   the program or in the current folder, import it silently and say so. Otherwise
   offer "Import settings from old MediaForge…" with a file picker.
2. If there's still no TMDB key, show a short welcome step. It has a link to
   themoviedb.org's API settings page, a field that accepts either the v3 API key
   or the v4 read access token, and a "Check key" button that calls
   `/configuration`. MediaForge stays usable without a key (parsed names only) and
   says so.
3. Offer, but don't force, adding MediaForge to the right-click menu.

## 7. Architecture

```
pyproject.toml
src/mediaforge/
  __main__.py          entry point: CLI parsing, single-instance handling, start UI
  app.py               composition root: build services, wire them to the UI
  models/              Pydantic v2 models (frozen where sensible)
  core/
    parser.py          filename to ParsedMedia
    formatter.py       format string + match to relative output path
    planner.py         queue items to a Plan (exact operations, collisions resolved)
    executor.py        runs a Plan, records every operation in the journal
    undo.py            reverses chosen operations safely
    cleanup.py         junk and orphan cleanup, empty-folder pruning
    matcher.py         confidence rules + remembered choices
    queue.py           the queue: items, background lookups, change events
  api/
    tmdb.py            async TMDB client (aiohttp)
    trailers.py        yt-dlp wrapper (asyncio subprocess), tool bootstrap
  db/
    database.py        connection, schema migrations
    repositories.py    settings, presets, cache, matches, history
    legacy_import.py   one-time JSON import
  ui/
    main_window.py, queue_view.py, detail_panel.py, history_view.py,
    settings_view.py, confirm_window.py, match_picker.py, first_start.py,
    theme.py, widgets/…, log_handler.py
  platform/
    single_instance.py, context_menu.py, paths.py
tests/
```

Rules:

- `core/`, `api/`, `db/` and `models/` must not import `tkinter`, `customtkinter` or
  anything in `ui/`. The UI talks to the rest only through the services that
  `app.py` builds. Everything below the UI is testable without a display.
- All I/O is async. Blocking file-system work (rename, mkdir, scandir on a slow NAS)
  runs through `asyncio.to_thread` so it never blocks the Tk event loop.
- Type hints everywhere. `pyright` (or `mypy --strict`) passes on everything except
  `ui/`, where CustomTkinter's missing stubs make strict checking impractical.

## 8. Behaviour details

### 8.1 Parsing (`core/parser.py`)

- **guessit** is the engine (my call; the blueprint allows either), wrapped so the
  rest of the app only sees `ParsedMedia`: `title`, `year`, `season`, `episode`,
  `episode_end` (for multi-episode files), `episode_title`, `kind` (movie/episode),
  `tags` (resolution, source, codec, audio, edition, release group), and `source_name`.
- The wrapper passes the preset's type (`movie`, `tv` or auto) to guessit as a hint.
  On "auto", a file counts as TV when a season or episode is found, matching the
  legacy app.
- Normalization kept from the legacy app: an episode-only file (`E05`, `Ep 5`)
  becomes season 1; dotted and underscored words become spaces; the human
  title-casing rule leaves ALL-CAPS tokens untouched and capitalizes other words.
- **Tests**: a table-driven suite of at least 60 real-world names, including every
  name in the owner's `mediaforge_undo.json` (copied into the test as literals,
  without the NAS path). It covers bracketed years, `1x02`, `S01E01-E02`,
  `S01E01E02`, episode-only names, year-in-title cases (`2001 A Space Odyssey`,
  `1917`, `Blade Runner 2049`), site tags like `[EZTVx.to]`, and the legacy
  `TAG_PATTERNS` tags. Where guessit gets a case wrong, the wrapper fixes it and the
  test documents why.

### 8.2 Formatting (`core/formatter.py`)

- The variables are case-insensitive, as in the legacy app: `{ny}`, `{t}`, `{s}`,
  `{e}`, `{s00e00}`, `{en}`. `{s}`/`{e}` are two-digit, `00` when unknown.
  Multi-episode gives `S01E01-E02` and `e` = `01-E02`. A missing episode name
  becomes `Episode 02`.
- `/` in the format separates folders. The file extension is appended, lower-cased.
- **Per-segment sanitizing** (a fix over the legacy app): remove `<>:"/\|?*` and
  control characters, normalize to NFKC, collapse spaces, strip trailing dots and
  spaces, and avoid reserved names (`CON`, `NUL`, `COM1`…) by appending `_`. An
  empty segment becomes `_`.
- An unknown `{variable}` is left as typed and flagged in the format preview line,
  so typos are visible.

### 8.3 Planning and executing a run

- **Destination base**: a file that came from a dropped folder uses **that** folder.
  A file added on its own uses its parent folder. (The legacy app used the first
  dropped folder for everything. Per-root is the intended behaviour; my call.)
- **Collisions**: if the target exists and isn't the same file, append ` (1)`,
  ` (2)`… If the file already has the target name, skip it ("already correctly
  named"). Two queued files that would land on the same name get suffixes in queue
  order. Collisions are resolved at plan time, so Preview shows the real final names.
- **Order** within a run: clean junk (if on) → per file: create folders, rename or
  move, posters, trailer → prune empty folders (if on).
- **Posters** (names kept from the legacy app, because cleanup depends on them):
  - movie: `{ny} - Poster.jpg` in the movie's destination folder;
  - series: `{ny} - Series Poster.jpg` in the series root (the first destination
    folder segment containing the series title, else the file's folder);
  - season: `Season NN Poster.jpg` in the episode's folder;
  - TMDB size `w780`. Never overwrite an existing poster.
- **Trailer**: search `ytsearch1:{title} {year} official trailer` and save as
  `{ny} - Trailer.mp4` in the destination folder. If the download fails, write
  `{ny} - Trailer.url` pointing at the YouTube **search results page** (the legacy
  app wrote an unusable `ytsearch1:` URL).
- **Preview** runs the planner and writes the plan to the Activity log ("would
  rename…", "would download poster…") without touching disk or network beyond
  lookups.
- Moves across volumes (for example a local disk to a NAS share) must work. Use
  `shutil.move` semantics when `os.replace` fails with a cross-device error.
- One failing file never stops the run. It's marked as an error and the run continues.
- Files may be processed with limited concurrency (builder's call). The journal must
  still record each file's operations in the order they happened.
- Preview never creates a History entry. A Forge run that changed nothing isn't
  recorded either.
- **Cancel**: a running Forge can be cancelled between files. Finished operations
  stay in the journal and are undoable.

### 8.4 Journal and undo (`core/executor.py`, `core/undo.py`, `db/`)

- Every change is written to the database **before** it happens (state `pending`),
  then marked `done` or `failed` right after. After a crash, the next start finds
  `pending` rows, checks the disk to decide whether each happened, and fixes the
  record. History is never left lying.
- The operation kinds are `rename` (src → dst), `create_file` (poster, trailer,
  `.url`; with size and modified time recorded), `create_dir`, `remove_dir` (from
  pruning) and `delete_file` (junk cleanup; recorded for the log but **not
  undoable**, and shown as such).
- Undo of a `rename`: proceed only if `dst` exists and `src` doesn't. Recreate the
  `src` parent folder if needed, then move back. Otherwise skip with a reason ("file
  was moved or renamed again", "something else now has the old name").
- Undo of a `create_file`: delete only if the file still exists **and** its size and
  modified time match what was recorded. Otherwise skip ("file has changed since").
- Undo of a `create_dir`: remove only if empty, after the other undos in the same
  batch.
- Undo of a `remove_dir`: recreate the empty folder. (The legacy app couldn't do
  this.)
- Within a batch, operations are undone in reverse order. Each undo is itself
  journaled, so the History view knows what's undone and the app survives a crash
  mid-undo.
- Retention: 50 runs. Older runs and their operations are deleted.

### 8.5 TMDB client (`api/tmdb.py`)

- One shared `aiohttp.ClientSession` with timeouts (10 s), a concurrency limit
  (about 8 in flight) and a retry with backoff on 429 and 5xx.
- Auth: a v3 key goes in the `api_key` query parameter. A v4 read access token (a
  JWT starting with `eyJ`) goes in the `Authorization: Bearer` header. The key is
  never logged; URLs are redacted in log lines.
- Endpoints and behaviour as in the legacy app: `/search/movie` with the year, then
  without, de-duplicated; `/search/tv` with `first_air_date_year`;
  `/tv/{id}/season/{n}` for episode names and the season poster; `/configuration` to
  validate a key; `include_adult=false`; the language from settings.
- Images: thumbnails `w92`, preview `w342`, downloads `w780`.
- Responses are cached in SQLite (search and season results 7 days; images on disk
  in the cache folder, 30 days). The legacy app cached in memory only.
- Response JSON is parsed into Pydantic models. Unknown fields are ignored; a missing
  required field means "no usable result", not a crash.

### 8.6 Match confidence (`core/matcher.py`)

The legacy rules, slightly loosened (my call):

1. A remembered user choice for (kind, normalized title, year) wins.
2. Confident: the normalized title equals the result's title or original title, and
   the year matches. Normalizing means casefold, `&` → `and`, dropping punctuation
   and collapsing spaces.
3. Exactly one result: confident.
4. Otherwise uncertain. Take the top result provisionally and mark it `?` (§6.2).

### 8.7 Logging

- The standard `logging` module only; no `print`.
- A file handler: a rotating log at `%LOCALAPPDATA%\MediaForge\logs\mediaforge.log`
  (1 MB × 5).
- A UI handler: thread-safe. Records go onto a queue that the Tk loop drains, and
  show in the Activity strip, coloured by level. Success messages use INFO with a
  `success` flag in `extra`.
- Unhandled exceptions in async tasks are logged with their traceback to the file
  and summarized in the UI.

### 8.8 Right-click menu (`platform/context_menu.py`)

- Settings → Right-click menu: pick which presets to show, then **Add to right-click
  menu** or **Remove**. This writes or removes keys under
  `HKEY_CURRENT_USER\Software\Classes` (for `*` files and for `Directory`), so **no
  administrator rights** are needed. The legacy app wrote an HKLM `.reg` file. A
  "Save as .reg file instead" option stays for people who prefer that.
- The structure is a "MediaForge" cascading menu with one entry per preset ("Forge:
  <preset>"). The command is
  `"<path to MediaForge.exe>" --confirm --preset "<preset>" "%1"`.
  `MultiSelectModel=Player` lifts Explorer's 15-item limit. When running from source,
  the command uses `pythonw.exe -m mediaforge`.
- After the program moves (for example a new version unzipped elsewhere), Settings
  detects that the registered path is stale and offers to fix it.
- On Windows 11 these entries sit under "Show more options". Adding to the new
  compact menu needs a signed package and is out of scope.
- The owner already has the legacy HKLM entries installed (pointing at
  `MediaForge.py --auto-run`). Settings detects them and offers to save a
  "remove old MediaForge menu" `.reg` file, because removing HKLM keys needs
  administrator rights. Until they're removed, they keep launching the old script,
  so the menu shows two "MediaForge" entries.

### 8.9 Single instance (`platform/single_instance.py`)

Explorer starts one process **per selected file**. To show one confirm window for all
of them:

- The first process becomes the primary. It holds a named mutex and listens on a
  per-user named pipe (`multiprocessing.connection` with an auth key stored in the
  user's app-data folder).
- Later processes send their arguments to the primary and exit immediately.
- The primary collects `--confirm` paths for a short quiet period (about 400 ms after
  the last one arrives), then opens **one** confirm window per preset.
- If the main window is already open, its process is the primary. Right-click
  confirms open as separate windows from it.
- Plain launches (no `--confirm`) bring the existing main window to the front, with
  any paths added to the queue.

### 8.10 Trailer tools (`api/trailers.py`)

- Tools are **not** bundled (my call: YouTube breaks old yt-dlp versions often). On
  first trailer use, MediaForge asks once ("Trailers need two free tools, about N MB.
  Download now?"). It then downloads to `%LOCALAPPDATA%\MediaForge\tools\`:
  - `yt-dlp.exe` from the official GitHub release, checked against that release's
    `SHA2-256SUMS`;
  - the Windows ffmpeg "gpl-shared" build from `yt-dlp/FFmpeg-Builds` (GitHub
    releases, about 90 MB), checked against its published `checksums.sha256`,
    keeping only its `bin` folder (`ffmpeg.exe`, `ffprobe.exe` and their DLLs).
- The checksum must match, or the file is deleted and the trailer step reports an
  error. Only these two HTTPS sources are allowed.
- yt-dlp updates itself (`yt-dlp -U`) at most once a week, and once more right after
  a failed download before giving up.
- It runs via `asyncio.create_subprocess_exec` with no shell, with the same format
  selection and merge settings as the legacy app (`bestvideo[ext=mp4]+bestaudio
  [ext=m4a]/best[ext=mp4]/best`, merge to mp4, YouTube client fallbacks). Progress
  lines are parsed for the row's status. If ffmpeg is missing, it falls back to
  `best[ext=mp4]` with no merging.

### 8.11 Storage locations

- Database: `%APPDATA%\MediaForge\mediaforge.db` (settings, presets, remembered
  matches, history, metadata cache index). WAL mode.
- Cache (images), logs and tools: `%LOCALAPPDATA%\MediaForge\…`.
- When run from source with `MEDIAFORGE_HOME` set, everything goes there instead (for
  development and tests).
- Nothing is written next to the program, so it works from a read-only or
  Program Files location.

### 8.12 Legacy import (`db/legacy_import.py`)

- The config import brings over the TMDB key, presets (types, formats and options
  map one to one: `chk_poster` → movie poster and so on), theme, and window size and
  position.
- The undo import becomes one History run, "Imported from old MediaForge", with its
  renames and created files.
- The import runs once and is recorded in settings. The JSON files are left
  untouched.

### 8.13 Command line

`MediaForge.exe [paths…] [--preset NAME] [--confirm]`

- With no flags, it opens the main window with the paths queued and the preset
  selected.
- `--confirm` opens the confirm flow (§6.4) instead.
- The legacy `--auto-run` (rename with no UI) is accepted for old context-menu
  entries and behaves like `--confirm`, because the owner wants confirmation first.

## 9. Packaging and release

- `pyproject.toml` declares all runtime and dev dependencies, with pinned minimum
  versions. A lock file (`uv.lock` or `requirements*.txt` with hashes) is the
  builder's choice.
- PyInstaller **one-folder** build (my call: it starts fast, which matters for
  right-click use, and antivirus programs flag it less than one-file builds). It
  bundles the tkdnd binaries, CustomTkinter assets, guessit/babelfish data files,
  `MediaForge.ico` and `MediaForge.png`. The legacy runtime download of icons from
  GitHub goes away.
- A build script produces `dist/MediaForge-<version>-win64.zip`.
- The version comes from one place (`mediaforge.__version__`) and appears in About
  and in the zip name. The first release is **1.0.0** (the legacy app was v0.5).
- Third-party license texts ship in the zip. guessit is LGPL-3.0, so it's collected
  as plain source files (PyInstaller `module_collection_mode="py"`), which keeps it
  replaceable. ffmpeg and yt-dlp are downloaded by the user's machine (§8.10), not
  redistributed.
- Leaving room for a future installer means not hard-coding the install location.
- Publishing the zip (for example to the owner's GitHub releases) is the owner's
  step. The build just produces the file.

## 10. Quality bar

- **pytest** with **pytest-asyncio**. Unit tests cover the parser table,
  formatter, sanitizer, planner (collisions, per-root bases), matcher, executor and
  undo (on real temp folders, including "file changed since" and crash-recovery of
  `pending` rows), cleanup and prune rules, legacy import (using a sanitized copy of
  the owner's JSON files, **with the API key replaced by a dummy**), the context
  menu key builder (strings only, no registry writes in tests), and the TMDB client
  against a fake server (`aioresponses` or an in-process aiohttp test server). There
  are no live network calls in tests.
- A UI smoke test starts the main window off-screen, adds a temp folder of fake
  videos and closes cleanly. It is skipped when no display is available.
- `ruff` (lint + format) and the type checker pass.
- Responsiveness check: the queue with 1,000 fake files stays scrollable while
  lookups run. The builder chooses how (virtualized rows, paging, lazy widget
  creation); the legacy app's 100-row cap shouldn't come back silently.

## 11. Left to the builder

The exact widget structure and spacing within the Studio design. The CLI parsing
library. The migration mechanism for the schema. Whether to vendor or depend on
async-tkinter-loop. The lock-file tool. The exact retry and backoff numbers. Test
fixtures. Commit granularity.

## Appendix A: legacy capabilities that must survive

1. Video extensions `.mkv .mp4 .avi .mov .m4v .wmv .flv`, matched case-insensitively.
2. Add by drag and drop (files and folders), "Folder" and "File" pickers, Clear
   queue, and remove a single item.
3. Recursive scan option. Toggling it re-scans the dropped folders.
4. Presets: select one to apply its type, format and options; create, overwrite and
   delete; the last preset can't be deleted. The five defaults are Auto (`{ny}`),
   Movie (Folder) (`{ny}/{ny}`, poster + trailer), Movie (Flat) (`{ny}`), Series
   (Folder) (`{ny}/{ny} - Season {s}/{ny} - {s00e00} - {en}`, series + season
   posters) and Series (Flat) (`{ny} - {s00e00} - {en}`).
5. A live format preview of the selected file's output name.
6. The queue grouped by destination folder or series, with episode counts and
   per-file status (done, error, pending).
7. A preview of the poster, official title and year, episode code and name, original
   filename, and "Watch trailer" (YouTube search in the browser).
8. Auto type detection (TV when a season or episode is found) and manual movie/TV
   override per preset.
9. TMDB search, the confident-match rule, the single-result rule, and the match picker
   with thumbnails, "Use this", "Skip" and "Use top result", with the choice reused.
10. Episode names from TMDB season data, sanitized, falling back to `Episode NN`.
11. Multi-episode files.
12. Renaming and moving into formatted folders, creating them as needed, with the
    ` (n)` collision suffix and "already correctly named" skip.
13. Movie, series and season posters with the legacy names, never overwriting.
14. Trailer download (with the improved `.url` fallback).
15. Clean junk: `Thumbs.db`, `.DS_Store`, `desktop.ini`; `.url` files whose base
    name has a matching video; `<base> - Poster.jpg` without a `<base>.<video>`;
    `… - Series Poster.jpg` in a folder tree with no videos; `Season NN Poster.jpg`
    in a folder with no videos.
16. Prune empty folders after a run: the source folders and dropped folders, deepest
    first.
17. Undo with a confirmation that states what will be reversed (now per run or per
    file, §6.3).
18. Settings: theme (Dark/Light/System), TMDB key with validation, presets, and
    right-click menu with a choice of presets.
19. Window size and position remembered.
20. Command line: paths and `--preset` (and `--auto-run`, see §8.13).
21. Shortcuts: `Enter` = Preview, `Ctrl+Enter` = Forge, `Ctrl+Z` = undo last run.
    They're ignored while typing in a text field.
22. A coloured activity log.
23. The app icon and logo.
