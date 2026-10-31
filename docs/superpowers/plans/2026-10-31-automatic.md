# Renaming on its own (1.3.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A finished download in a watched folder is looked up, named with the Series or Movies
preset and moved into that library folder on its own when MediaForge is sure, waits in the Inbox
when it isn't, and MediaForge keeps doing this from the tray, started with Windows.

**Architecture:** A pure finished-file watcher (`core/watch.py`) and a pure sure-or-not decision
plus library plan (`core/auto.py`) feed an `AutoRenamer` service that runs on the one asyncio
loop, adds watched files to the existing `Queue` (so the Inbox shows them), and renames through the
existing `Executor` (journal before act, History, Undo) with the new run source "automatic". The
UI gains Inbox sections, a Settings › Library tab, a watching light, and a pystray tray icon on
its own thread, as in AirGrabber.

**Tech Stack:** Python 3.13, CustomTkinter 6 + async-tkinter-loop, aiosqlite, Pydantic, guessit,
Pillow, pystray (new), PowerShell WScript.Shell for the Startup shortcut.

**Spec:** `docs/superpowers/specs/2026-10-31-family-look-and-automatic-design.md` (stage 2, "Renaming
on its own"; the owner's decisions 5, 6, 8, 9, 10)

## Global Constraints

- Everything in `CLAUDE.md` holds: one asyncio loop, `gc_guard`, guessit parsing on the loop in
  chunks (never in a thread), long paths via `fsops`, `prepare_popup`, no `transient()`, the window
  shown with `update()`, tests with `MEDIAFORGE_HOME` in a temp dir, never the real registry,
  Startup folder, right-click menu or data folders.
- The one exception to "no threads" is pystray's message loop (as in AirGrabber's
  `app/ui/tray.py`): its callbacks only hand work to the loop with `call_soon_threadsafe` and never
  touch Tk.
- Never touch a file outside the watched folders and the Series/Movies library folders; never a
  file that is still downloading; never overwrite; every change journaled before it happens.
- Moved, not copied (owner's decision 10): a move across shares is copy-then-delete as
  `fsops.move_file` already does.
- Shows and movies are never guessed: a file the file name and TMDB don't agree on waits in the
  Inbox.
- Every folder is a setting (decision 9); nothing is assumed, nothing is watched until the owner
  sets it up.
- Install and update stay as in 1.2: new dependency `pystray>=0.19` in `pyproject.toml` (the
  launcher installs it); `uninstall.ps1` stays Windows PowerShell 5.1-safe and ASCII.
- Existing data carries over: new settings fields have defaults; `RunSource` is stored as text,
  so `"automatic"` needs no schema change.
- English texts, plain words; the owner is not a programmer.

## Review Focus

1. **A download that only looks finished:** a stalled torrent whose file is already full size
   (pre-allocated) and untouched for a while, or Download Station's temporary names: it must not
   be moved. The quiet rule, the exclusive open and trial mode are the guards (Task 2, Task 6).
2. **The NAS going away mid-way:** a look or a move while `\\NAS\...` is unreachable: nothing half
   moved, the file waits in "Needs you" with the reason, it is retried on a later look, and the
   log gets one line per outage, not one per look (Task 6).
3. **Loops:** a watched folder inside, equal to or containing a library folder; a renamed file
   that lands back in a watched folder; an automatic rename undone in History (the file is back in
   the download folder): nothing is ever moved twice on its own (Tasks 1, 3, 6).
4. **Already in the library:** the same episode or movie already there: never a "(1)" copy on its
   own; it waits as "Already in the library" (Task 4).
5. **Running for days in the tray:** "Done today" empties at midnight, the daily summary comes
   once a day, a downloaded update still installs (quiet restart into the tray), and the Inbox
   doesn't grow without end (Tasks 3, 6, 9).

---

### Task 1: Settings for the library, and the "automatic" run source

**Files:**
- Modify: `src/mediaforge/db/settings.py`, `src/mediaforge/models/history.py`,
  `src/mediaforge/ui/history_view.py` (label), `src/mediaforge/db/history.py`,
  `src/mediaforge/db/database.py` (schema 2), `src/mediaforge/core/executor.py`,
  `src/mediaforge/ui/queue_view.py` and `src/mediaforge/ui/confirm_window.py` (pass the kinds)
- Create: `src/mediaforge/core/library.py`
- Test: `tests/core/test_library.py`, `tests/db/test_settings.py`, `tests/db/test_history.py`

**Interfaces:**
- Produces: `AppSettings` fields `watch_folders: list[str] = []`, `series_folder: str = ""`,
  `movies_folder: str = ""`, `series_preset: str = "Series (Folder)"`,
  `movies_preset: str = "Movie (Folder)"`, `automatic: Literal["off", "trial", "on"] = "off"`,
  `clean_downloads: bool = True`, `notify_needs: bool = True`, `daily_summary: bool = True`,
  `summary_sent_on: str = ""` (ISO date), `tray_notice_shown: bool = False`.
- Produces: `RunSource.AUTOMATIC = "automatic"`; History labels it "automatic".
- Produces: `core/library.py`: `library_problems(settings: AppSettings) -> list[str]` (plain
  sentences; empty = ready to watch); `inside(path: Path, folder: Path) -> bool` (case-insensitive,
  `\\server\share` aware); `watched_root(path: Path, settings: AppSettings) -> Path | None`.
- Produces: `HistoryRepo.was_undone(src: Path) -> bool`: a RENAME whose `src` is this path was
  undone (any run).
- Produces: runs remember which kinds they hold (for History's Series | Movies switch, deferred
  from 1.2): `SCHEMA_VERSION = 2` adds `runs.kinds TEXT NOT NULL DEFAULT ''` (comma-separated
  `"episode"`/`"movie"`; old runs keep `''`, shown on both sides);
  `HistoryRepo.start_run(source, preset_name, started_at=None, kinds: Collection[MediaKind] = ())`;
  `RunSummary.kinds: frozenset[MediaKind]`; `Executor.run(..., kinds: Collection[MediaKind] = ())`
  passes them on; the Inbox's forge and the confirm window pass their items' kinds.

- [ ] **Step 1: Write the failing tests**
  - `test_new_settings_have_defaults_and_old_settings_still_load` (an old JSON document without the
    new keys loads; `automatic == "off"`, presets "Series (Folder)" / "Movie (Folder)").
  - `test_library_problems` with these cases and exact texts:
    no watch folder → "Choose a download folder to watch.";
    no Series folder → "Choose the Series library folder.";
    no Movies folder → "Choose the Movies library folder.";
    a watch folder equal to, inside or containing a library folder →
    "A watched folder can't be inside a library folder, or the other way round.";
    all set and separate → `[]`. Paths compared case-insensitively, `\\NAS\Multimedia\Downloads`
    vs `\\nas\multimedia` counts as inside.
  - `test_watched_root_finds_the_folder_a_file_came_from`.
  - `test_was_undone` (rename recorded, undone → True; not undone → False; other path → False).
  - `test_automatic_runs_are_labelled` (a RunCard for source AUTOMATIC shows "automatic").
  - `test_runs_remember_their_kinds` and `test_a_version_1_database_gains_kinds` (a database made
    with schema 1 opens, its runs have no kinds).
- [ ] **Step 2:** `PY -m pytest tests/core/test_library.py tests/db -q` — Expected: FAIL (missing
  module / fields).
- [ ] **Step 3: Implement** the fields, `RunSource.AUTOMATIC`, `core/library.py` (pure; uses
  `os.path.normcase` on both sides, compares path parts), `was_undone` (one SELECT on
  `operations` with `kind='rename' AND src=? AND undo_state='done'`), the History label, the
  schema-2 migration (`ALTER TABLE runs ADD COLUMN kinds ...` when `user_version < 2`).
- [ ] **Step 4:** same command, then `PY -m pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: library settings and the automatic run source`.

### Task 2: The finished-file watcher

**Files:**
- Create: `src/mediaforge/core/watch.py`
- Test: `tests/core/test_watch.py`

**Interfaces:**
- Consumes: `core.parser.is_video`, `core.fsops.walk`, `core.fsops.long_path`.
- Produces:
  - `LOOK_INTERVAL_S = 300` (a look every 5 minutes), `QUIET_S = 600` (untouched for 10 minutes).
  - `TEMP_SUFFIXES = (".part", ".!qb", ".crdownload", ".tmp", ".partial", ".download", ".aria2")`
    and `SKIP_FOLDERS = frozenset({"@eadir", "#recycle", "#snapshot", "$recycle.bin"})`.
  - `is_sample(path: Path) -> bool` (`\bsample\b` in the stem, case-insensitive).
  - `candidates(root: Path) -> list[Path]` (blocking; run with `asyncio.to_thread`): videos under
    `root`, skipping `SKIP_FOLDERS`, hidden/dot folders, temp suffixes (also a video with a sibling
    `<name>.aria2` or `<name>.part`), names starting with `~` or `.`, and samples. Raises `OSError`
    when `root` can't be read.
  - `class Watcher` with `__init__(self, *, now: Callable[[], float] = time.time,
    stat: Callable[[Path], tuple[int, int]] = fsops.stamp, can_open: Callable[[Path], bool] =
    exclusive_open)` and `finished(self, files: Sequence[Path]) -> list[Path]`: a file counts once
    its (size, mtime_ns) is the same as at the previous look, `now - mtime >= QUIET_S`, and
    `can_open` succeeds; files no longer listed are forgotten; a file that vanished between listing
    and stat is skipped, not an error.
  - `exclusive_open(path: Path) -> bool`: opens for reading with no sharing (`CreateFileW` share
    mode 0 through ctypes); `False` on a sharing violation or any `OSError`.

- [ ] **Step 1: Write the failing tests** (temporary folders, a fake clock, a fake `can_open`):
  `test_a_new_file_waits_for_a_second_look`, `test_a_growing_file_is_not_finished`,
  `test_a_file_touched_recently_is_not_finished` (same size twice but mtime 5 min ago),
  `test_a_locked_file_is_not_finished`, `test_temporary_and_sample_files_are_never_candidates`
  (`ep.mkv.part`, `ep.!qB`, `ep.mkv` + `ep.mkv.aria2`, `Sample/ep-sample.mkv`, `@eaDir/x.mkv`,
  `.hidden/x.mkv`, `~x.mkv`), `test_files_in_subfolders_count`,
  `test_a_vanished_file_is_forgotten`, `test_an_unreadable_root_raises_oserror`.
- [ ] **Step 2:** `PY -m pytest tests/core/test_watch.py -q` — Expected: FAIL (no module).
- [ ] **Step 3: Implement** `core/watch.py` (stdlib + ctypes only).
- [ ] **Step 4:** same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat: tell a finished download from one still coming in`.

### Task 3: Watched files in the queue

**Files:**
- Modify: `src/mediaforge/core/queue.py`
- Test: `tests/core/test_queue.py`

**Interfaces:**
- Consumes: `Watcher.finished` output (paths) with their watched root.
- Produces:
  - `ItemStatus.HELD = "held"`: needs the owner for a reason in `item.message` (not a TMDB choice).
  - `QueueItem.watched: Path | None = None` (the watched folder it came from) and
    `QueueItem.run_id: int | None = None` (the run that renamed it).
  - `Queue.add_watched(files: Sequence[tuple[Path, Path]]) -> list[int]` — `(file, watched root)`
    pairs; files already in the queue (same path) are skipped; parsed with `TypeHint.AUTO` whatever
    `self.hint` is; looked up like dropped files; returns the new ids.
  - `Queue.set_hint` re-parses only items with `watched is None`.
  - `Queue.mark_finished(item_id, new_path, run_id: int | None = None)` stores `run_id`.
  - `Queue.hold(item_id: int, reason: str) -> None` (status HELD, message, an "updated" event).
  - `Queue.purge_finished(before: float) -> int` removes finished items with `finished_at < before`
    (the Inbox's "Done today" rolls over at midnight; the queue doesn't grow for ever).
  - `Queue.forget_watched(present: set[str]) -> int` removes pending watched items whose path key
    isn't in `present` (the file went away).
  - `Queue.rescan` keeps watched items (only dropped folders and files are scanned again).

- [ ] **Step 1: Write the failing tests:**
  `test_watched_files_are_parsed_without_the_presets_hint` (hint TV, `Dune.2021.mkv` watched →
  movie), `test_set_hint_leaves_watched_files_alone`, `test_a_watched_file_is_added_once`,
  `test_hold_keeps_a_reason`, `test_purge_finished_drops_yesterdays_done`,
  `test_forget_watched_drops_files_that_went_away`, `test_mark_finished_keeps_the_run`,
  `test_rescan_keeps_watched_files`.
- [ ] **Step 2:** `PY -m pytest tests/core/test_queue.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** in `core/queue.py`.
- [ ] **Step 4:** same command — Expected: PASS; then `PY -m pytest -q` (the Inbox and confirm
  window use the queue) — Expected: PASS.
- [ ] **Step 5: Commit** `feat: watched files in the queue`.

### Task 4: Sure or not, and where it goes

**Files:**
- Create: `src/mediaforge/core/auto.py` (pure parts only in this task)
- Test: `tests/core/test_auto_decide.py`

**Interfaces:**
- Consumes: `QueueItem`, `MatchState`, `normalize_title`, `build_plan`, `PlanInput`, `Preset`,
  `AppSettings` (Task 1).
- Produces:
  - `class Decision(NamedTuple): sure: bool; reason: str` and
    `decide(item: QueueItem) -> Decision`. Sure only when:
    - REMEMBERED (the owner confirmed this title before), or
    - CONFIDENT, the TMDB title or original title equals the parsed title (`normalize_title`), and
      - an episode has season and episode numbers in its name and TMDB knows the episode
        (`episode_title` from TMDB is not None), or
      - a movie has a year in its name equal to TMDB's year.
    - Otherwise not sure, with exactly one reason: "TMDB isn't sure which one it is." (UNCERTAIN),
      "Not found at TMDB." (NOT_FOUND), "No TMDB key, or TMDB can't be reached." (OFFLINE),
      "Skipped before." (SKIPPED), "No year in the name: is it a movie?" (movie without year),
      "No season or episode number in the name." (episode without numbers),
      "TMDB doesn't know this episode." (episode not in TMDB), "The name and TMDB don't agree."
      (title mismatch).
  - `class LibraryRun(NamedTuple): kind: MediaKind; preset: Preset; plan: Plan;
    held: dict[int, str]` and
    `library_runs(items: Sequence[QueueItem], settings: AppSettings, presets: Sequence[Preset],
    exists: Callable[[Path], bool] = fsops.exists) -> list[LibraryRun]`: one per kind present;
    inputs with `root` = that kind's library folder and `dropped_folder=None`; the kind's preset
    format and options with `clean_junk=False, prune_empty=False`. A file whose planned target is
    not the wanted name (something already has it) is left out of the plan and put in `held` with
    "Already in the library." A missing preset falls back to the built-in default of that name.
  - `owner_runs(...)`: the same as `library_runs` but keeps "(1)" targets (an explicit Rename
    click on a held file may add a numbered copy, as 1.x does for dropped files).
  - Assertion inside both: every planned source is inside a watched folder and every target inside
    its library folder (`core.library.inside`), else `ValueError` (never reached in normal use).

- [ ] **Step 1: Write the failing tests:** one `decide` test per reason above plus
  `test_a_remembered_match_is_sure`, `test_a_confident_episode_is_sure`,
  `test_a_confident_movie_with_its_year_is_sure`;
  `test_shows_and_movies_go_to_their_own_folder_with_their_own_preset`,
  `test_an_existing_show_folder_is_reused` (target under the existing
  `<Series>\VisionQuest\VisionQuest - Season 01`),
  `test_already_in_the_library_is_held_not_numbered`,
  `test_an_owner_click_may_number_a_copy`, `test_a_target_outside_the_library_is_refused`.
- [ ] **Step 2:** `PY -m pytest tests/core/test_auto_decide.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the pure parts of `core/auto.py`.
- [ ] **Step 4:** same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat: decide when a download is safe to rename on its own`.

### Task 5: Clearing up a download's leftovers

**Files:**
- Modify: `src/mediaforge/core/cleanup.py`, `src/mediaforge/models/plan.py`,
  `src/mediaforge/core/executor.py`, `src/mediaforge/core/auto.py`
- Test: `tests/core/test_cleanup.py`, `tests/core/test_executor.py`, `tests/core/test_auto_decide.py`

**Interfaces:**
- Produces:
  - `download_leftovers(folder: Path) -> list[Path]`: in `folder` and below, sample videos
    (`watch.is_sample`), `.nfo`, `.txt`, `.url`, `.sfv` and `JUNK_NAMES`; nothing else (subtitles,
    other videos and pictures stay).
  - `PlannedLeftovers(folder: Path, item_ids: tuple[int, ...])` and `Plan.leftovers:
    tuple[PlannedLeftovers, ...] = ()`.
  - `Executor` handles `plan.leftovers` after the files, only for a folder whose items all
    succeeded and which then holds no other video: each leftover a journaled DELETE_FILE, then the
    folder and empty subfolders journaled REMOVE_DIR (deepest first). Never the watched root itself.
  - `library_runs` adds `PlannedLeftovers` for each moved file's folder when
    `settings.clean_downloads` and that folder is below (not equal to) its watched root.

- [ ] **Step 1: Write the failing tests:** `test_download_leftovers_are_samples_and_notes_only`,
  `test_leftovers_go_after_a_successful_move` (release folder removed, ops journaled),
  `test_leftovers_stay_when_a_move_failed`, `test_leftovers_stay_when_another_video_is_left`,
  `test_the_watched_folder_itself_is_never_removed`,
  `test_no_leftover_plan_when_clean_downloads_is_off`.
- [ ] **Step 2:** `PY -m pytest tests/core -q` — Expected: FAIL (the new ones).
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `PY -m pytest tests/core -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: clear a download's leftovers after moving it`.

### Task 6: The automatic renamer

**Files:**
- Modify: `src/mediaforge/core/auto.py`, `src/mediaforge/app.py` (Services gets `auto` and
  `run_lock`)
- Test: `tests/core/test_auto_renamer.py`

**Interfaces:**
- Consumes: Tasks 1–5; `Executor.run(plan, source=, preset_name=, on_progress=, cancel=)`;
  `PresetRepo.list()`; `HistoryRepo.list_runs()`.
- Produces:
  - `WatchState = Literal["off", "trial", "watching", "unreachable", "setup"]` and
    `class WatchStatus(NamedTuple): state: WatchState; text: str` (e.g. "Watching 1 folder",
    "Trial: watching 1 folder", "Can't reach \\NAS\Multimedia", "Set up watching in Settings").
  - `class AutoRenamer` with `__init__(self, services: Services, *, now: Callable[[], float] =
    time.time, interval_s: float = LOOK_INTERVAL_S)`, and:
    - `start() -> None` / `async stop() -> None` (stop waits for a running look),
      `reconfigure() -> None` (settings changed: start, stop or look soon), `look_soon() -> None`.
    - Runs pass `kinds` to `Executor.run`.
    - `async look() -> LookResult` (one look; tests call it directly): for each watched root,
      `candidates` in a thread → `Watcher.finished` → skip paths inside a library folder → files
      that `history.was_undone` go in held ("You undid this once; click Rename to move it again.")
      → `queue.add_watched` → `queue.wait_idle()` → `decide` each new or retried item: not sure →
      `queue.hold` (UNCERTAIN keeps NEEDS_CHOICE); sure and `automatic == "on"` → under
      `services.run_lock`, `library_runs` and `Executor.run(source=RunSource.AUTOMATIC)` per kind,
      marking items finished with the run id, failures → `hold(item, "Couldn't move it: <error>")`
      and retried on the next look; sure and `"trial"` → left READY ("Would rename").
      Then `queue.forget_watched(seen)` and `queue.purge_finished(<today's midnight>)`.
    - `async rename(ids: Sequence[int], *, source: RunSource) -> RunResult` for the Inbox's
      Rename/Forge on watched items (uses `owner_runs`).
    - `on_status: Callable[[WatchStatus], None]`, `on_needs: Callable[[int], None]` (the count of
      watched items needing the owner, after each look), `notify: Callable[[str, str], None]`.
  - `LookResult(NamedTuple): added: int; renamed: int; held: int; unreachable: list[Path]`.
  - `needs_message(new: int) -> tuple[str, str]` → ("2 files need you", "Open MediaForge to pick
    or confirm them.") (1 → "1 file needs you"); sent once per look with new held/uncertain items
    when `notify_needs`.
  - `summary_message(episodes: int, movies: int) -> str` → "Today: 5 episodes and 1 movie renamed"
    (only the parts above zero), and `summary_due(now: datetime, sent_on: str) -> bool`: from
    `SUMMARY_HOUR = 20` (8 pm), once per day, only when today's automatic runs renamed something
    (counted from History, so it survives restarts); `summary_sent_on` saved after sending.
  - Unreachable roots or library folders: status "unreachable", logged once when it starts and once
    when it ends ("Can reach \\NAS\Multimedia again"), never per look.
  - `Services.run_lock: asyncio.Lock` (`field(default_factory=asyncio.Lock)`; one run at a time,
    shared with the Inbox) and `Services.auto: AutoRenamer` (`field(init=False)`, set in
    `build_services` right after the `Services` is made, since it takes the services).

- [ ] **Step 1: Write the failing tests** (temporary folders as downloads and library, fake TMDB
  from `tests/helpers/fake_tmdb.py`, fake clock; files' mtimes set old with `os.utime`):
  `test_a_sure_episode_is_moved_into_the_series_folder`,
  `test_a_sure_movie_is_moved_into_the_movies_folder`, `test_trial_moves_nothing`,
  `test_an_unsure_match_waits_with_its_reason`, `test_a_file_still_coming_in_waits`,
  `test_an_unreachable_library_holds_and_retries` (library folder removed, then restored),
  `test_an_unreachable_download_folder_changes_the_status`,
  `test_an_undone_file_is_not_moved_again`, `test_files_in_a_library_folder_are_ignored`,
  `test_one_automatic_run_per_kind_per_look` (History source "automatic"),
  `test_the_needs_notification_comes_once_per_batch`, `test_summary_message_texts`,
  `test_summary_due_once_a_day_after_eight`, `test_unreachable_is_logged_once`,
  `test_done_today_rolls_over_at_midnight`, `test_rename_by_the_owner_moves_a_held_file`.
- [ ] **Step 2:** `PY -m pytest tests/core/test_auto_renamer.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `AutoRenamer` and wire `Services.auto`, `Services.run_lock` in
  `build_services` (not started there; `app.run` starts it in Task 9).
- [ ] **Step 4:** same command, then `PY -m pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: rename and move finished downloads on their own`.

### Task 7: The Inbox and the top band for watched files

**Files:**
- Modify: `src/mediaforge/ui/queue_view.py`, `src/mediaforge/ui/main_window.py`,
  `src/mediaforge/ui/history_view.py`, `src/mediaforge/ui/widgets/virtual_list.py` (an "undo"
  band action style only if needed)
- Test: `tests/ui/test_queue_view.py`, `tests/ui/test_shell.py`, `tests/ui/test_history_view.py`

**Interfaces:**
- Consumes: Task 3 (`watched`, HELD, `run_id`), Task 6 (`auto.rename`, `run_lock`, `on_status`,
  `WatchStatus`).
- Produces:
  - `build_inbox_rows(items, fmt, kind, *, naming: Callable[[QueueItem], tuple[str, Path]] | None
    = None, waiting_title: str = "Would rename", today: date | None = None)`: sections in this
    order: **"Needs you"** (dropped files not renamed yet, and watched items NEEDS_CHOICE, HELD or
    ERROR, the band subtitle showing the item's reason), **waiting_title** (watched items looking
    up, ready or working), **"Done today"** (finished today; earlier ones not shown). `naming`
    gives a watched item its kind preset's format and library folder, so its band says where it
    will go ("→ Series › VisionQuest › VisionQuest - Season 01"). `needs` counts only "Needs you".
  - Done bands get an **Undo** action (undoes those items' RENAME operations of `item.run_id`
    through `UndoService.undo_operations`; the items leave the Inbox; a watched file is then "undone
    once" and waits under "Needs you" on the next look).
  - Rename/Forge on watched items go through `services.auto.rename(ids, source=RunSource.APP)`;
    dropped files keep 1.x's behaviour. `forge` and the automatic renamer share `run_lock`.
  - `HistoryView.set_kind(kind)` shows only runs holding that kind (`RunSummary.kinds`; a run
    without kinds, from before 1.3, shows on both sides) and reloads; its empty text says "No
    renamed shows yet" / "No renamed movies yet".
  - `MainShell.set_watch_status(status: WatchStatus) -> None`: the right of the top band shows a
    dot (success colour watching, warning colour trial, error colour unreachable, muted off/setup)
    and the text; clicking it opens Settings › Library. With automatic "off" it reads "Drop files
    here" as in 1.2.

- [ ] **Step 1: Write the failing tests:** `test_watched_files_show_where_they_will_go`,
  `test_held_files_are_under_needs_you_with_their_reason`,
  `test_trial_files_are_under_would_rename`, `test_done_today_hides_yesterday`,
  `test_needs_counts_only_what_needs_you`, `test_rename_on_a_watched_band_moves_it_to_the_library`,
  `test_undo_on_a_done_band_puts_the_file_back`, `test_the_watching_light_follows_the_status`,
  `test_forge_waits_for_an_automatic_run` (`run_lock` held → forge starts after it is released),
  `test_history_follows_the_switch` (an episode run, a movie run and an old run without kinds).
  Keep the 1,000-file responsiveness test green.
- [ ] **Step 2:** `PY -m pytest tests/ui/test_queue_view.py tests/ui/test_shell.py
  tests/ui/test_history_view.py -q` —
  Expected: FAIL (the new ones).
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** same command, then `PY -m pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: the Inbox shows what is watched, waiting and done today`.

### Task 8: Settings › Library, and start with Windows

**Files:**
- Create: `src/mediaforge/ui/library_tab.py`, `src/mediaforge/platform/startup.py`
- Modify: `src/mediaforge/ui/settings_view.py` (tab list, builders), `src/mediaforge/uninstall.ps1`
  (remove the Startup shortcut)
- Test: `tests/ui/test_settings_view.py`, `tests/platform/test_startup.py`, `tests/test_scripts.py`

**Interfaces:**
- Consumes: Task 1 settings and `library_problems`; Task 6 `auto.reconfigure()`.
- Produces: `platform/startup.py` (port of AirGrabber's `app/services/startup.py`):
  `SHORTCUT_NAME = "MediaForge.lnk"`, `is_enabled(*, startup_dir: Path | None = None) -> bool`,
  `async set_enabled(on: bool, *, startup_dir: Path | None = None) -> None` (raises
  `StartupError` with a plain sentence), the shortcut running `pythonw.exe "<install>\main.py"
  --tray` with MediaForge's icon and `APP_USER_MODEL_ID` through `shortcut_app_id.cs`.
  Development copies (a folder with `tests/` or `.git`) refuse with "Only an installed
  MediaForge can start with Windows." `uninstall.ps1` removes
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MediaForge.lnk` when it points at
  this install's `main.py` (the existing `Remove-Shortcut` helper).
- Produces: `SettingsView.tab_names == ["Library", "Naming", "TMDB", "Right-click menu", "Looks",
  "About"]`, "Library" first and shown first. `LibraryTab(master, view: SettingsView)` with:
  - **Download folders to watch**: a list with Add folder… / Remove (folder dialog).
  - **Series library folder** and **Movies library folder**, each with Choose…, and the preset each
    uses (dropdowns of the preset names).
  - **Rename on its own**: segmented Off / Trial / On; choosing On the first time when it was Off
    goes to Trial first and says so ("Trial first: MediaForge shows what it would do; switch to On
    when you're happy."). Disabled with the `library_problems` sentences shown while any remain.
  - **Clean up downloads after moving** (samples, .nfo, .txt, .url, empty folders).
  - **Start with Windows** (in the tray).
  - **Tell me when files need me** and **A summary in the evening**.
  - Every change saves at once and calls `services.auto.reconfigure()`.

- [ ] **Step 1: Write the failing tests:** `test_settings_has_its_tabs` (updated list, "Library"
  shown first), `test_library_folders_are_saved`, `test_rename_on_its_own_waits_for_the_folders`,
  `test_on_goes_through_trial_first`, `test_overlapping_folders_are_refused_with_the_reason`,
  `test_start_with_windows_uses_the_startup_shortcut` (fake startup folder),
  `test_startup_shortcut_spec` (paths, `--tray`, icon, app id), `test_startup_enable_and_disable`
  (fake folder, fake PowerShell runner), `test_development_copies_dont_start_with_windows`,
  `test_uninstaller_removes_the_startup_shortcut` (script text, as the existing installer tests).
- [ ] **Step 2:** `PY -m pytest tests/ui/test_settings_view.py tests/platform/test_startup.py
  tests/test_scripts.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** (folder dialogs through `filedialog.askdirectory` behind a
  replaceable `ask_folder` attribute, as the existing pickers, so tests never open dialogs).
- [ ] **Step 4:** same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Settings › Library, and start with Windows`.

### Task 9: The tray, and staying up to date

**Files:**
- Create: `src/mediaforge/ui/tray.py`
- Modify: `src/mediaforge/platform/cli.py` (`--tray`), `src/mediaforge/app.py` (start hidden,
  tray, auto start, notifications), `src/mediaforge/ui/main_window.py` (close → tray, quiet
  update restart), `src/mediaforge/launch.py` (relaunch into the tray), `pyproject.toml`
  (`pystray>=0.19`)
- Test: `tests/ui/test_tray.py`, `tests/test_launch.py`, `tests/ui/test_shell.py`

**Interfaces:**
- Produces:
  - `LaunchArgs.tray: bool` from `--tray` (and handed over: a second start with `--tray` while one
    runs does nothing visible).
  - `ui/tray.py` (port of AirGrabber's `TrayIcon`/`TrayHost`, without its TrayNotice window):
    `TrayIcon(loop, image, *, on_open, on_check_now, on_quit, backend=None)` with `start()`,
    `stop()`, `notify(title, message)`, `set_count(n: int)` (tooltip "MediaForge · 2 files need
    you" and a small count badge drawn on the icon with Pillow); menu: Open MediaForge (default),
    Check downloads now, Quit.
  - Close behaviour: when automatic is not "off" and the tray icon runs, closing the window hides
    it (the first time a notification says "MediaForge keeps watching your downloads in the tray.",
    then `tray_notice_shown = True`); otherwise close quits as in 1.2. Quit from the tray waits for
    a running rename (as `may_close`).
  - `--tray` start: the window is built and withdrawn; a second start (or Open in the tray menu)
    shows it.
  - Updates while hidden: the update check repeats every 24 hours; when an update is staged and
    the window is hidden and nothing runs, MediaForge restarts into the tray
    (`RunOutcome(restart=True, hidden=True)`, `LaunchHooks.relaunch(tray: bool)` adds `--tray`).

- [ ] **Step 1: Write the failing tests:** `test_tray_args`, `test_tray_menu_actions_reach_the_loop`
  (fake pystray backend), `test_tray_count_tooltip`, `test_close_hides_to_tray_when_watching`,
  `test_close_quits_when_not_watching`, `test_tray_start_keeps_the_window_hidden`,
  `test_a_staged_update_restarts_into_the_tray_when_hidden`,
  `test_relaunch_into_the_tray_passes_tray`.
- [ ] **Step 2:** `PY -m pytest tests/ui/test_tray.py tests/test_launch.py tests/ui/test_shell.py
  -q` — Expected: FAIL.
- [ ] **Step 3: Implement**; `app.run` starts `services.auto` when automatic isn't "off", connects
  `on_status` → `shell.set_watch_status`, `on_needs` → `tray.set_count` and the Inbox badge,
  `notify` → `tray.notify`; `services.auto.stop()` before `close_services`.
- [ ] **Step 4:** same command, then `PY -m pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: keep watching from the tray`.

### Task 10: Release 1.3.0

**Files:**
- Modify: `src/mediaforge/__init__.py` (`__version__ = "1.3.0"`), `README.md`, `CLAUDE.md`
- Test: whole suite; real-window check

- [ ] **Step 1:** README: a short "Renaming on its own" part (set the folders in Settings ›
  Library, trial first, the tray, start with Windows). CLAUDE.md: a "Renaming on its own (1.3)"
  section naming `core/watch.py`, `core/auto.py`, `core/library.py`, `ui/tray.py`,
  `platform/startup.py`, the run lock, and the pystray-thread exception.
- [ ] **Step 2: Real-window check** with `MEDIAFORGE_HOME` in a temp dir, two temp folders as
  "downloads" and two as libraries, the fake picture fetch: trial with an episode, a movie and an
  unsure file (screenshots of Inbox and Settings › Library, dark and light); then On (the files
  move, "Done today", Undo on a band); the tray icon and its menu; close → tray → open again.
- [ ] **Step 3:** `PY -m ruff check src tests`, `PY -m ruff format src tests`, `PY -m pyright
  --pythonpath <PY>`, `PY -m pytest -q` — Expected: clean, all pass.
- [ ] **Step 4: Commit** `release: 1.3.0`. Publishing waits for the owner's OK; before pushing,
  check the outgoing commits for the NAS name and personal paths.

## Calls in this plan (for the owner to check)

- A look at the download folders every **5 minutes**; a file counts as finished once it has been
  untouched for **10 minutes**, looks the same twice in a row and isn't held open by anything.
  No reliance on Windows' change alerts (they get missed on a NAS); "Check downloads now" in the
  tray menu looks at once.
- Turning it on also handles downloads **already sitting** in the folder; trial shows them first.
- A movie is only renamed on its own when its file name has a **year** that matches TMDB; an
  episode only when its name has the season and episode and TMDB knows that episode. Everything
  else waits for one click.
- If the same episode or movie is **already in the library**, the new file waits in the Inbox
  ("Already in the library") instead of becoming a "(1)" copy.
- Clean-up removes samples, .nfo, .txt, .url files and the emptied release folder; **subtitles and
  pictures stay** where they are.
- The evening summary comes at **8 pm** (or the first look after it), only on days something was
  renamed.
- Undoing an automatic rename puts the file back in the download folder and MediaForge **won't
  move it on its own again**; it waits until you click Rename.
- Closing the window keeps MediaForge in the tray only while renaming on its own is Trial or On;
  otherwise closing quits, as now.
