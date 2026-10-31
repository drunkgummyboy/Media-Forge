# MediaForge Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild MediaForge from scratch as a modular, async, typed Windows app with
the Studio UI, a right-click confirm window, a 50-run selective-undo history, and a
standalone PyInstaller build.

**Architecture:** One asyncio event loop runs everything. Tk is pumped by
`async_tkinter_loop.main_loop(root)` inside `asyncio.run(...)`. `core/`, `api/`,
`db/` and `models/` are UI-free and fully unit-tested. `app.py` wires the services
together, and `ui/` only calls services. Every disk change goes through a SQLite
journal before it happens, which is what makes undo and crash recovery work.

**Tech Stack:** Python 3.13, customtkinter 6.0, tkinterdnd2 0.7, async-tkinter-loop
0.10, aiohttp 3.14, aiosqlite 0.22, pydantic 2.14, guessit 4.4, Pillow; dev: pytest 9,
pytest-asyncio 1.4, aioresponses 0.7, ruff, pyright, PyInstaller 6.22.

**Spec:** `docs/superpowers/specs/2026-10-25-mediaforge-rewrite-design.md` (the
"spec"; § numbers below refer to it). The legacy reference is `MediaForge.py`.

## Global Constraints

- Windows only. Python `>=3.13`. Package lives in `src/mediaforge/` (src layout).
  Version `1.0.0` in `mediaforge.__version__`.
- `core/`, `api/`, `db/`, `models/`, `platform/` never import `tkinter`,
  `customtkinter`, `tkinterdnd2` or `mediaforge.ui`. Task 1 adds a test that
  enforces this.
- No runtime `pip`/`subprocess` installs. No `print()` in `src/` (ruff rule `T20`).
- Never overwrite a user file. File moves use `core/fsops.py` only.
- Never log the TMDB key. All logging goes through `logging`, with the redacting
  filter from Task 13 installed.
- Dev venv lives **outside Google Drive**: `%LOCALAPPDATA%\MediaForge-dev\venv`
  (already created, with all packages installed). Run tools as
  `"$LOCALAPPDATA/MediaForge-dev/venv/Scripts/python.exe" -m <tool>` from the
  project root. Later steps write this as `PY -m …`.
- Tests set `MEDIAFORGE_HOME` to a temp dir (autouse fixture), so they never touch
  real `%APPDATA%`. No live network in tests.
- Video extensions: `.mkv .mp4 .avi .mov .m4v .wmv .flv` (case-insensitive).
- History limit: `50` runs.
- Studio palette (dark): main `#16181d`, rail/title `#111317`, surface `#1e2128`,
  raised `#262a33`, border `#2c303a`, text `#e8eaed`, muted `#8b93a1`, accent
  `#f2a33a`, success `#4cc38a`, error `#e5534b`.
- Commit after each task with a `feat:`/`test:`/`chore:` message ending in the line
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Case-only renames** (`the office (2005).mkv` → `The Office (2005).mkv`): Windows
   reports that the target "exists" because it's the same file. Expected: rename
   happens, with no ` (1)` suffix. Pinned in Task 6 (planner) and Task 7 (fsops).
2. **The NAS share drops mid-run** (UNC path suddenly unreachable). Expected: each
   affected file fails with a clear message, the run continues and finishes, and the
   journal shows `failed`, not `pending`. Pinned in Task 7.
3. **A locked file** (being played or scanned by Plex). Expected: a
   `PermissionError` marks just that file as an error, and the rest of the run
   carries on. Pinned in Task 7.
4. **Paths longer than 260 characters** (deep series folders + long episode names
   on the NAS). Expected: works through extended-length paths, and never truncates
   silently. Pinned in Task 7.
5. **The same file added twice** (dropped on its own and inside a dropped folder,
   or dropped twice). Expected: it appears once in the queue, and the dropped
   folder wins as its root. Pinned in Task 11.

---

### Task 1: Project skeleton, paths, guard-rails

**Files:**
- Create: `pyproject.toml`, `src/mediaforge/__init__.py`, `src/mediaforge/platform/__init__.py`, `src/mediaforge/platform/paths.py`, `tests/conftest.py`, `tests/test_paths.py`, `tests/test_layering.py`
- Modify: `CLAUDE.md` (add a "Dev setup" section: venv path, `PY -m pytest`, `PY -m ruff check src tests`, `PY -m pyright`)

**Interfaces:**
- Produces: `mediaforge.__version__ = "1.0.0"`;
  `AppPaths(data_dir: Path, local_dir: Path)` (frozen dataclass) with properties
  `db_file`, `logs_dir`, `cache_dir`, `images_dir`, `tools_dir`,
  `instance_key_file`, and `ensure() -> None`; `app_paths(env: Mapping[str, str] = os.environ) -> AppPaths`.

- [ ] **Step 1: Write `pyproject.toml`**

  The build backend is hatchling. Runtime deps: `customtkinter>=6.0`,
  `tkinterdnd2>=0.7`, `async-tkinter-loop>=0.10`, `aiohttp>=3.14`,
  `aiosqlite>=0.22`, `pydantic>=2.14`, `guessit>=4.4`, `pillow>=13`. The `dev` extra:
  `pytest>=9`, `pytest-asyncio>=1.4`, `aioresponses>=0.7`, `ruff`, `pyright`,
  `pyinstaller>=6.22`. Script entry: `mediaforge = "mediaforge.__main__:main"`. Pytest
  config: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, and the marker `ui`. Ruff
  config: `select = ["E","F","I","UP","B","T20","ASYNC"]`, line length 100. Pyright
  config: `strict` on `src/mediaforge` excluding `src/mediaforge/ui`.

- [ ] **Step 2: Install editable** — `PY -m pip install -e ".[dev]"`. Expected: it
  exits 0.

- [ ] **Step 3: Write the failing tests**

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def mediaforge_home(tmp_path, monkeypatch):
    home = tmp_path / "mfhome"
    monkeypatch.setenv("MEDIAFORGE_HOME", str(home))
    return home

# tests/test_paths.py
def test_home_override_puts_everything_under_home(tmp_path):
    p = app_paths({"MEDIAFORGE_HOME": str(tmp_path)})
    assert p.db_file == tmp_path / "mediaforge.db"
    assert p.logs_dir == tmp_path / "logs"
    assert p.tools_dir == tmp_path / "tools"
    assert p.images_dir == tmp_path / "cache" / "images"

def test_default_uses_appdata_and_localappdata():
    p = app_paths({"APPDATA": r"C:\R", "LOCALAPPDATA": r"C:\L"})
    assert p.db_file == Path(r"C:\R\MediaForge\mediaforge.db")
    assert p.logs_dir == Path(r"C:\L\MediaForge\logs")

def test_ensure_creates_dirs(tmp_path):
    p = app_paths({"MEDIAFORGE_HOME": str(tmp_path / "x")}); p.ensure()
    assert p.logs_dir.is_dir() and p.images_dir.is_dir() and p.tools_dir.is_dir()

# tests/test_layering.py
NON_UI = ["core", "api", "db", "models", "platform"]
def test_non_ui_packages_never_import_tk():
    # walk src/mediaforge/<pkg>/**/*.py, parse with ast, collect imported module names
    # assert none starts with tkinter, customtkinter, tkinterdnd2, async_tkinter_loop, mediaforge.ui
```

- [ ] **Step 4: Run** — `PY -m pytest tests/test_paths.py tests/test_layering.py -v`. Expected: the paths tests FAIL (ImportError).
- [ ] **Step 5: Implement `app_paths` and `AppPaths`** in `src/mediaforge/platform/paths.py`. Use `MEDIAFORGE_HOME` (both dirs = that path) if set, else `APPDATA\MediaForge` and `LOCALAPPDATA\MediaForge`.
- [ ] **Step 6: Run** — `PY -m pytest -v` and `PY -m ruff check src tests`. Expected: everything passes.
- [ ] **Step 7: Commit** — `chore: project skeleton, app paths, layering guard`

---

### Task 2: Filename parser

**Files:**
- Create: `src/mediaforge/models/__init__.py`, `src/mediaforge/models/media.py`, `src/mediaforge/core/__init__.py`, `src/mediaforge/core/parser.py`, `tests/core/test_parser.py`

**Interfaces:**
- Produces (`models/media.py`):
  - `class TypeHint(StrEnum): AUTO="auto"; MOVIE="movie"; TV="tv"`
  - `class MediaKind(StrEnum): MOVIE="movie"; EPISODE="episode"`
  - `class ParsedMedia(BaseModel, frozen=True)`: `source_name: str`, `kind: MediaKind`, `title: str`, `year: int | None = None`, `season: int | None = None`, `episode: int | None = None`, `episode_end: int | None = None`, `episode_title: str | None = None`, `tags: tuple[str, ...] = ()`
- Produces (`core/parser.py`): `VIDEO_EXTENSIONS: frozenset[str]`, `is_video(path: Path) -> bool`, `parse_filename(name: str, hint: TypeHint = TypeHint.AUTO) -> ParsedMedia`, `human_title(text: str) -> str`

- [ ] **Step 1: Write the failing table test**

  Use `@pytest.mark.parametrize` over rows of `(name, hint, title, year, season,
  episode, episode_end, episode_title, kind)`. These rows are required (expected
  values were verified against guessit 4.4 with the options in Step 3):

| name | title | year | S | E | E-end | ep title | kind |
|---|---|---|---|---|---|---|---|
| `VisionQuest.S01E02.Theseus.1080p.DSNP.WEB-DL.ENG.Atmos.ITA.DDP5.1.H264-TBK.mkv` | VisionQuest | – | 1 | 2 | – | Theseus | episode |
| `VisionQuest.S01E01.No.Strings.1080p.DSNP.WEB-DL.ENG.Atmos.ITA.DDP5.1.H264-TBK.mkv` | VisionQuest | – | 1 | 1 | – | No Strings | episode |
| `Gold Rush S17E03 Diagnosing Rick 1080p AMZN WEB-DL DDP2 0 H 264-NTb[EZTVx.to].mkv` | Gold Rush | – | 17 | 3 | – | Diagnosing Rick | episode |
| `Lanterns (2026) - S01E08 - _.mkv` | Lanterns | 2026 | 1 | 8 | – | None | episode |
| `Lanterns.2026.S01E08.1080p.WEB.h264-ETHEL[EZTVx.to].mkv` | Lanterns | 2026 | 1 | 8 | – | None | episode |
| `Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.HDR.H.265-FLUX.mkv` | Dune Part Two | 2024 | – | – | – | None | movie |
| `Spider-Man.No.Way.Home.2021.1080p.WEB-DL.mkv` | Spider-Man No Way Home | 2021 | – | – | – | None | movie |
| `the.last.of.us.s01e03.long.long.time.1080p.web.h264-glhf.mkv` | The Last Of Us | – | 1 | 3 | – | Long Long Time | episode |
| `Show.Name.1x02.Episode.Title.HDTV.x264-LOL.avi` | Show Name | – | 1 | 2 | – | Episode Title | episode |
| `Show.Name.S01E01-E02.720p.WEB.mkv` | Show Name | – | 1 | 1 | 2 | None | episode |
| `Show.Name.S01E01E02.720p.WEB.mkv` | Show Name | – | 1 | 1 | 2 | None | episode |
| `Anime Show - E05 [1080p].mkv` | Anime Show | – | 1 | 5 | – | None | episode |
| `Anime Show Ep 12.mkv` | Anime Show | – | 1 | 12 | – | None | episode |
| `2001.A.Space.Odyssey.1968.1080p.BluRay.x264.mkv` | 2001 A Space Odyssey | 1968 | – | – | – | None | movie |
| `1917.2019.2160p.UHD.BluRay.x265-TERMiNAL.mkv` | 1917 | 2019 | – | – | – | None | movie |
| `Blade.Runner.2049.2017.1080p.BluRay.x264.mkv` | Blade Runner 2049 | 2017 | – | – | – | None | movie |
| `Inception (2010) [1080p] [BluRay] [5.1] [YTS.MX].mp4` | Inception | 2010 | – | – | – | None | movie |
| `Kill.Bill.Vol.1.2003.mkv` | Kill Bill Vol 1 | 2003 | – | – | – | None | movie |
| `Back.to.the.Future.Part.II.1989.mkv` | Back To The Future Part II | 1989 | – | – | – | None | movie |
| `Doctor.Who.2005.S01E01.Rose.1080p.mkv` | Doctor Who | 2005 | 1 | 1 | – | Rose | episode |
| `Fast & Furious 9 (2021).mkv` | Fast & Furious 9 | 2021 | – | – | – | None | movie |
| `Severance.S02E10.Cold.Harbor.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-FLUX.mkv` | Severance | – | 2 | 10 | – | Cold Harbor | episode |

  Add at least 38 more rows (to reach 60) covering `[YTS]`-style brackets,
  `.DIRECTORS.CUT.`, `REPACK`, `PROPER`, `HEVC`, `AV1`, `10bit`, `DTS-HD.MA`,
  `S2024E01`-style dates, all-caps titles (`NCIS` stays `NCIS`), and mixed-case
  titles (`VisionQuest` stays `VisionQuest`). Run guessit first, then write down what
  it produces after the Step 3 rules. Where that's wrong, add a wrapper fix plus a
  comment in the test.

  Separate tests:

```python
def test_hint_movie_forces_movie_kind():
    assert parse_filename("Show.S01E02.mkv", TypeHint.MOVIE).kind is MediaKind.MOVIE
def test_hint_tv_forces_episode_kind_even_without_numbers():
    assert parse_filename("Movie Title.mkv", TypeHint.TV).kind is MediaKind.EPISODE
def test_tags_collected():
    p = parse_filename("The.Matrix.1999.REMASTERED.1080p.BluRay.x264.TrueHD.7.1.Atmos-FGT.mkv")
    assert {"1080p", "Remastered"} <= set(p.tags)
def test_human_title_rules():
    assert human_title("the last of us") == "The Last Of Us"
    assert human_title("VisionQuest NCIS mIxEd") == "VisionQuest NCIS mIxEd"
def test_is_video_case_insensitive():
    assert is_video(Path("a.MKV")) and not is_video(Path("a.srt"))
```

- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_parser.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement `parse_filename`**. Call `guessit(name, opts)` with
  `opts = {"excludes": ["part", "volume", "country", "release_group", "website", "streaming_service", "language", "subtitle_language"]}`.
  Add `opts["type"] = "movie"` for `TypeHint.MOVIE` and `"episode"` for `TypeHint.TV`.
  - `kind`: MOVIE for the MOVIE hint, EPISODE for the TV hint. For AUTO, EPISODE when
    guessit returns a season or episode, else MOVIE.
  - An `episode` list gives `episode=first`, `episode_end=last`.
  - An EPISODE with an episode but no season gets `season=1`.
  - `title` and `episode_title` go through `human_title`. An empty title becomes the
    file stem with `._` replaced by spaces, or `"_"`.
  - `tags` is the string values of `screen_size, source, video_codec, audio_codec,
    edition, other` (lists flattened).
  - `human_title` capitalizes the first letter of words that are entirely lowercase
    and leaves every other word alone. (This deliberately differs from the legacy
    rule, which lower-cased the rest of each word and broke `VisionQuest`.)
- [ ] **Step 4: Run** the parser tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: guessit-based filename parser with real-name table`

---

### Task 3: Presets and the format engine

**Files:**
- Create: `src/mediaforge/models/preset.py`, `src/mediaforge/core/formatter.py`, `tests/core/test_formatter.py`
- Modify: `src/mediaforge/models/media.py` (add `MatchState`, `ResolvedMedia`)

**Interfaces:**
- Consumes: `ParsedMedia`, `MediaKind`, `TypeHint` (Task 2).
- Produces (`models/media.py`):
  - `class MatchState(StrEnum): CONFIDENT; REMEMBERED; UNCERTAIN; NOT_FOUND; SKIPPED; OFFLINE` (values lowercase names)
  - `class ResolvedMedia(BaseModel, frozen=True)`: `parsed: ParsedMedia`, `title: str`, `year: int | None`, `episode_title: str | None`, `match_state: MatchState`, `tmdb_id: int | None = None`, `poster_path: str | None = None`, `season_poster_path: str | None = None`; classmethod `offline(parsed, state=MatchState.OFFLINE) -> ResolvedMedia` (copies the parsed title, year and episode_title); properties `kind`, `season`, `episode`, `episode_end`, and `ny` (`"Title (Year)"` or `"Title"`).
- Produces (`models/preset.py`):
  - `class PresetOptions(BaseModel)`: `movie_poster`, `series_poster`, `season_poster`, `trailer`, `clean_junk`, `prune_empty` (all `bool = False`)
  - `class Preset(BaseModel)`: `name: str`, `type: TypeHint`, `format: str`, `options: PresetOptions`
  - `DEFAULT_PRESETS: tuple[Preset, ...]` (the five in spec Appendix A §4, in that order)
  - `LEGACY_OPTION_KEYS: dict[str, str]` (`chk_poster→movie_poster`, `chk_series_poster→series_poster`, `chk_season_poster→season_poster`, `chk_trailer→trailer`, `chk_clean→clean_junk`, `chk_prune→prune_empty`)
- Produces (`core/formatter.py`): `KNOWN_VARIABLES = ("ny","t","s","e","s00e00","en")`; `sanitize_segment(seg: str) -> str`; `class Rendered(BaseModel, frozen=True)`: `parts: tuple[str, ...]`, `unknown: tuple[str, ...]`, property `relative: PurePath`; `render(fmt: str, media: ResolvedMedia, extension: str) -> Rendered`.

- [ ] **Step 1: Write the failing tests**

```python
def ep(**kw) -> ResolvedMedia: ...  # helper: VisionQuest (2026) S01E02 "Theseus"
def test_series_folder_preset():
    r = render("{ny}/{ny} - Season {s}/{ny} - {s00e00} - {en}", ep(), ".MKV")
    assert r.parts == ("VisionQuest (2026)", "VisionQuest (2026) - Season 01",
                       "VisionQuest (2026) - S01E02 - Theseus.mkv")
def test_variables_case_insensitive(): assert render("{NY}", movie(), ".mp4").parts == ("Dune Part Two (2024).mp4",)
def test_multi_episode(): assert render("{s00e00}", ep(episode=1, episode_end=2), ".mkv").parts == ("S01E01-E02.mkv",)
def test_missing_values(): assert render("{t} {s00e00} {en}", ep(season=None, episode=None, episode_title=None), ".mkv").parts == ("VisionQuest S00E00 Episode 00.mkv",)
def test_no_year(): assert render("{ny}", movie(year=None), ".mkv").parts == ("Dune Part Two.mkv",)
def test_values_are_not_rescanned(): assert render("{en}", ep(episode_title="{t}"), ".mkv").parts == ("{t}.mkv",)
def test_unknown_variable_kept_and_reported():
    r = render("{ny} {foo}", movie(), ".mkv"); assert r.parts == ("Dune Part Two (2024) {foo}.mkv",); assert r.unknown == ("foo",)
def test_backslash_and_slash_both_split(): assert len(render("{t}\\{t}/{t}", movie(), ".mkv").parts) == 3
def test_empty_segments_dropped(): assert render("/{t}//", movie(), ".mkv").parts == ("Dune Part Two.mkv",)
@pytest.mark.parametrize("raw,clean", [
    ('Who: Are "You"?', "Who Are You"), ("Part 1...", "Part 1"), ("  a   b  ", "a b"),
    ("CON", "CON_"), ("nul", "nul_"), ("COM1", "COM1_"), ("", "_"), ("a\x07b", "ab"), ("ｆｕｌｌ", "full")])
def test_sanitize_segment(raw, clean): assert sanitize_segment(raw) == clean
def test_episode_title_with_slash_does_not_create_folder():
    assert render("{en}", ep(episode_title="AC/DC"), ".mkv").parts == ("ACDC.mkv",)
```

- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_formatter.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Substitute in a single regex pass over the format
  (`re.sub(r"\{([A-Za-z0-9]+)\}", repl, fmt)`, where the lookup is lower-cased and
  unknown names are returned unchanged and collected). Split the result on `/` and
  `\`, but only on separators that came **from the format, not from values**. Do
  this by sanitizing each value with `sanitize_segment` before substitution, then
  sanitizing each segment again. Append the lower-cased extension to the last
  segment after sanitizing it. Values: `s` and `e` are `:02d` or `"00"`; `e` adds
  `-E{end:02d}` for multi-episode; `en` falls back to `f"Episode {e}"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: presets model and format engine with per-segment sanitizing`

---

### Task 4: Database, settings, presets, remembered matches, cache

**Files:**
- Create: `src/mediaforge/db/__init__.py`, `db/database.py`, `db/settings.py`, `db/presets.py`, `db/matches.py`, `db/cache.py`, `tests/db/test_database.py`, `tests/db/test_repos.py`

**Interfaces:**
- Consumes: `Preset`, `PresetOptions`, `DEFAULT_PRESETS` (Task 3), `MediaKind` (Task 2).
- Produces:
  - `class Database`: `@classmethod async open(cls, path: Path) -> Database`; `conn: aiosqlite.Connection`; `async close()`. WAL mode, `foreign_keys=ON`, schema version kept in `PRAGMA user_version` (`SCHEMA_VERSION = 1`). It creates every table, including `runs` and `operations` (the columns are in Task 5).
  - `class AppSettings(BaseModel)`: `tmdb_key: str = ""`, `tmdb_language: str = "en-US"`, `theme: Literal["Dark","Light","System"] = "Dark"`, `scan_subfolders: bool = True`, `window_geometry: str | None = None`, `last_preset: str | None = None`, `legacy_imported: bool = False`, `context_menu_presets: list[str] = []`, `trailer_tools_ok: bool = False`, `ytdlp_checked_at: float = 0.0`, `first_start_done: bool = False`.
  - `class SettingsRepo(db)`: `async load() -> AppSettings` (defaults when missing); `async save(s: AppSettings) -> None`.
  - `class LastPresetError(Exception)`. `class PresetRepo(db)`: `async list() -> list[Preset]` (by position); `async save(p: Preset, *, old_name: str | None = None) -> None` (rename keeps position; new name goes last); `async delete(name) -> None` (raises `LastPresetError` when one is left); `async seed_defaults_if_empty() -> None`; `async replace_all(presets: Sequence[Preset]) -> None`.
  - `class RememberedMatch(BaseModel)`: `kind: MediaKind`, `norm_title: str`, `year: int | None`, `tmdb_id: int | None` (None = "skip TMDB"), `label: str`. `class MatchRepo(db)`: `async get(kind, norm_title, year) -> RememberedMatch | None`; `async remember(m: RememberedMatch) -> None` (upsert); `async list() -> list[RememberedMatch]`; `async forget(kind, norm_title, year)`; `async clear()`.
  - `class CacheRepo(db)`: `async get_json(key: str, max_age_s: float) -> Any | None`; `async put_json(key: str, value: Any) -> None`; `async purge(older_than_s: float) -> int`.

- [ ] **Step 1: Write the failing tests** (`db` fixture: `await Database.open(tmp_path/"t.db")`, closed after the test):
  `test_open_sets_wal_and_version` (`PRAGMA journal_mode` → `wal`, `user_version` → 1);
  `test_open_twice_is_idempotent`;
  `test_settings_roundtrip_and_defaults` (load on an empty DB equals `AppSettings()`; save, then load, gives the same values);
  `test_presets_seed_defaults_order` (names are `["Auto","Movie (Folder)","Movie (Flat)","Series (Folder)","Series (Flat)"]`);
  `test_preset_rename_keeps_position`;
  `test_cannot_delete_last_preset` (`pytest.raises(LastPresetError)`);
  `test_match_upsert_and_none_year` (a `year=None` key round-trips and is distinct from `year=2005`);
  `test_cache_expiry` (patch `time.time`: a value older than `max_age_s` gives `None`; `purge` returns 1).
- [ ] **Step 2: Run** — `PY -m pytest tests/db -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** In SQL, store a `None` year as `0` (so primary keys work), and map it back in the repo. Settings are stored as a single JSON row, key `"app"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: sqlite database with settings, presets, remembered matches, cache`

---

### Task 5: History journal repository

**Files:**
- Create: `src/mediaforge/models/history.py`, `src/mediaforge/db/history.py`, `tests/db/test_history.py`

**Interfaces:**
- Consumes: `Database` (Task 4).
- Produces (`models/history.py`): `OpKind(StrEnum)`: `RENAME="rename"`, `CREATE_FILE="create_file"`, `CREATE_DIR="create_dir"`, `REMOVE_DIR="remove_dir"`, `DELETE_FILE="delete_file"`; `OpState`: `PENDING, DONE, FAILED`; `UndoState`: `PENDING, DONE, SKIPPED`; `RunSource`: `APP="app"`, `RIGHT_CLICK="right-click"`, `IMPORT="import"`;
  `Operation(BaseModel)`: `id, run_id, seq: int`, `kind: OpKind`, `src: str | None`, `dst: str | None`, `size: int | None`, `mtime_ns: int | None`, `state: OpState`, `error: str | None`, `undo_state: UndoState | None`, `undo_note: str | None`, `undone_at: datetime | None`; property `undoable` (DONE, not DELETE_FILE, `undo_state` not DONE).
  `RunSummary(BaseModel)`: `id`, `started_at: datetime`, `finished_at: datetime | None`, `source: RunSource`, `preset_name: str | None`, `file_count` (DONE renames), `created_count` (DONE create_file), `undoable_count`, `undone_count`; property `status -> Literal["active","partly-undone","undone"]`.
  `HISTORY_LIMIT = 50`.
- Produces (`db/history.py`) `class HistoryRepo(db)`: `async start_run(source: RunSource, preset_name: str | None, started_at: datetime | None = None) -> int`; `async record_pending(run_id: int, kind: OpKind, src: Path | None, dst: Path | None) -> int`; `async mark_done(op_id: int, size: int | None = None, mtime_ns: int | None = None)`; `async mark_failed(op_id: int, error: str)`; `async finish_run(run_id: int) -> bool` (sets `finished_at`; **deletes** the run and returns False when it has no DONE ops; enforces `HISTORY_LIMIT` by deleting the oldest runs); `async list_runs() -> list[RunSummary]` (newest first); `async operations(run_id) -> list[Operation]` (by seq); `async get_operations(ids: Sequence[int]) -> list[Operation]`; `async pending_operations() -> list[Operation]`; `async pending_undos() -> list[Operation]`; `async mark_undo(op_id: int, state: UndoState, note: str | None = None)` (DONE sets `undone_at`).

  Table columns: `runs(id INTEGER PRIMARY KEY, started_at REAL, finished_at REAL, source TEXT, preset_name TEXT)`; `operations(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER, kind TEXT, src TEXT, dst TEXT, size INTEGER, mtime_ns INTEGER, state TEXT, error TEXT, undo_state TEXT, undo_note TEXT, undone_at REAL)`. (Add them to `Database` in Task 4 if not already there.)

- [ ] **Step 1: Write the failing tests:**
  `test_run_lifecycle` (start, pending, done, finish → `list_runs()[0].file_count == 1`);
  `test_empty_run_is_dropped` (`finish_run` returns False and `list_runs()` is empty);
  `test_retention_keeps_50` (create 52 finished runs; 50 remain and the two oldest are gone, ops cascaded);
  `test_status_partly_undone_and_undone`;
  `test_seq_increments_per_run`;
  `test_pending_operations_listed`.
- [ ] **Step 2: Run** — `PY -m pytest tests/db/test_history.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `HistoryRepo`. `seq` = `COALESCE(MAX(seq),0)+1` within the run, inside the same transaction.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: journaled run history with 50-run retention`

---

### Task 6: Planner (targets, collisions, posters, trailers, prune list)

**Files:**
- Create: `src/mediaforge/models/plan.py`, `src/mediaforge/core/planner.py`, `tests/core/test_planner.py`

**Interfaces:**
- Consumes: `render`, `sanitize_segment` (Task 3); `ResolvedMedia`, `PresetOptions` (Task 3).
- Produces (`models/plan.py`): `PlanInput(BaseModel, frozen)`: `item_id: int`, `source: Path`, `root: Path`, `dropped_folder: Path | None`, `media: ResolvedMedia`; `PlannedPoster`: `kind: Literal["movie","series","season"]`, `tmdb_path: str`, `target: Path`; `PlannedTrailer`: `query: str`, `title: str`, `year: int | None`, `target_stem: Path`; `PlannedFile`: `item_id: int`, `source: Path`, `target: Path`, `skip_reason: str | None`; `Plan`: `files: tuple[PlannedFile, ...]`, `posters: tuple[PlannedPoster, ...]`, `trailers: tuple[PlannedTrailer, ...]`, `clean_roots: tuple[Path, ...]`, `prune_dirs: tuple[Path, ...]`, `options: PresetOptions`.
- Produces (`core/planner.py`): `build_plan(inputs: Sequence[PlanInput], fmt: str, options: PresetOptions, exists: Callable[[Path], bool] = os.path.lexists) -> Plan`; `describe(plan: Plan) -> list[str]`; `ALREADY_NAMED = "already correctly named"`.

- [ ] **Step 1: Write the failing tests** (a fake `exists` backed by a set of lower-cased path strings):
  - `test_target_under_root`: a root `R`, the series format → `target == R/"VisionQuest (2026)"/"VisionQuest (2026) - Season 01"/"VisionQuest (2026) - S01E02 - Theseus.mkv"`.
  - `test_each_dropped_folder_is_its_own_root` (two inputs with different roots each land under their own root).
  - `test_collision_with_existing_file_gets_suffix` (target exists → `"… (1).mkv"`; both `(1)` and the base exist → `(2)`).
  - `test_two_queue_items_same_target` (the second gets ` (1)`, in input order).
  - `test_already_named_is_skipped` (`skip_reason == ALREADY_NAMED`).
  - **Review focus 1:** `test_case_only_rename_is_not_a_collision`: source `R/"the office (2005).mkv"`, target `R/"The Office (2005).mkv"`, `exists` true for the lower-case form → `target.name == "The Office (2005).mkv"`, `skip_reason is None`.
  - `test_posters`: movie poster at `target.parent/"Dune Part Two (2024) - Poster.jpg"`; series poster at the series root (the first folder segment containing the title, case-insensitive) named `"VisionQuest (2026) - Series Poster.jpg"`; the season poster at `target.parent/"Season 01 Poster.jpg"`; posters planned only when the option is on **and** a poster path is present; de-duplicated across episodes; skipped when the target exists.
  - `test_trailer_query_and_dedupe` (`query == "ytsearch1:Dune Part Two 2024 official trailer"`, `target_stem == target.parent/"Dune Part Two (2024) - Trailer"`; two episodes in one folder give one trailer).
  - `test_prune_dirs_deepest_first_and_clean_roots_only_dropped_folders`.
  - `test_describe_lines` (contains `"Rename: <old> → <new>"`, `"Skip: <name> (already correctly named)"`, `"Poster: <path>"`).
- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_planner.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** For collisions, keep a `taken` set of `os.path.normcase(str(path))`. A candidate is free when it's not taken and either `exists(c)` is false or `normcase(c) == normcase(source)`. Skip only on an **exact** string match of target and source. The suffix comes before the extension: `f"{stem} ({n}){suffix}"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: planner with per-root targets, collision handling, artwork plans`

---

### Task 7: Safe file operations and the executor

**Files:**
- Create: `src/mediaforge/core/fsops.py`, `src/mediaforge/core/executor.py`, `src/mediaforge/core/cleanup.py`, `src/mediaforge/core/recovery.py`, `tests/core/test_fsops.py`, `tests/core/test_cleanup.py`, `tests/core/test_executor.py`, `tests/core/test_recovery.py`

**Interfaces:**
- Consumes: `Plan` (Task 6), `HistoryRepo`, `OpKind`, `RunSource` (Task 5).
- Produces (`core/fsops.py`) — all sync, called via `asyncio.to_thread`: `long_path(p: Path) -> str` (adds `\\?\` or `\\?\UNC\` to absolute paths, idempotent); `move_file(src: Path, dst: Path) -> None` (raises `FileExistsError` if `dst` exists unless it is the same file case-insensitively; `os.rename`, falling back to copy-then-delete on `ERROR_NOT_SAME_DEVICE`/`EXDEV`, where the copy uses an exclusive create); `make_dirs(path: Path) -> list[Path]` (returns the folders it actually created, top-most first); `write_new_file(path: Path, data: bytes) -> tuple[int, int]` (`open(..., "xb")`, returns `(size, mtime_ns)`); `stamp(path: Path) -> tuple[int, int]`; `remove_file(path)`; `remove_empty_dir(path) -> bool`.
- Produces (`core/cleanup.py`): `JUNK_NAMES = frozenset({"thumbs.db", ".ds_store", "desktop.ini"})`; `find_cleanup_targets(root: Path) -> list[Path]` (the rules of spec Appendix A §15, case-insensitive).
- Produces (`core/executor.py`): `class ProgressEvent(BaseModel)`: `item_id: int | None`, `status: Literal["working","done","error","skipped"]`, `message: str`, `fraction: float`; `class RunResult(BaseModel)`: `run_id: int | None`, `renamed`, `skipped`, `failed`, `posters`, `trailers`, `cleaned`, `pruned: int`, `cancelled: bool`, `errors: list[str]`; `class PosterFetcher(Protocol)`: `async def image(self, path: str, size: str) -> bytes`; `class TrailerDownloader(Protocol)`: `async def download(self, trailer: PlannedTrailer) -> Path | None` (returns the created file: an `.mp4`, or the `.url` fallback); `class Executor(history: HistoryRepo, posters: PosterFetcher | None, trailers: TrailerDownloader | None)` with `async run(plan: Plan, *, source: RunSource, preset_name: str | None, on_progress: Callable[[ProgressEvent], None], cancel: asyncio.Event) -> RunResult`.
- Produces (`core/recovery.py`): `async recover(history: HistoryRepo) -> int` — settles each `pending` op from what's on disk (§8.4) and each pending undo the same way, and returns the count.

- [ ] **Step 1: Write the failing fsops tests:** `test_move_refuses_overwrite`; `test_case_only_rename_works` (Review focus 1: `a.mkv` → `A.mkv`, so `os.listdir` shows `A.mkv`); `test_long_path_roundtrip` (Review focus 4: build a 300+ character nested path under `tmp_path`, then `make_dirs`, `write_new_file` and `move_file` all succeed); `test_long_path_unc_prefix` (`long_path(Path(r"\\nas\share\a"))` is `r"\\?\UNC\nas\share\a"`); `test_make_dirs_reports_created_only`; `test_write_new_file_never_overwrites`.
- [ ] **Step 2: Write the failing cleanup tests:** one test per Appendix A §15 rule, plus `test_cleanup_keeps_poster_with_matching_video`.
- [ ] **Step 3: Write the failing executor tests** (real temp folders, a real `HistoryRepo`, fake fetcher/downloader):
  `test_rename_run_journals_everything` (ops are `create_dir`… then `rename`, all `done`, with `size`/`mtime_ns` recorded for created files);
  `test_order_clean_then_files_then_prune`;
  `test_one_failure_does_not_stop_run`;
  **Review focus 3:** `test_locked_file_is_error_not_crash` (monkeypatch `fsops.move_file` to raise `PermissionError` for one source → `failed == 1`, the others are renamed, the op state is `failed` with the error text);
  **Review focus 2:** `test_share_disappears_mid_run` (after the first file, the fake `move_file` raises `OSError(53, "The network path was not found")` for the rest → the run finishes, `failed == n-1`, `pending_operations()` is empty);
  `test_cancel_between_files` (set `cancel` in the first progress callback → `cancelled`, the first file's ops are done, no further renames);
  `test_poster_not_overwritten` (an existing poster file → no `create_file` op);
  `test_junk_deletes_are_journaled_as_delete_file`;
  `test_empty_run_leaves_no_history`.
- [ ] **Step 4: Write the failing recovery tests:** `test_recover_pending_rename_done_and_failed` (insert pending rename ops: one whose `dst` exists → done; one whose `src` still exists → failed with `"interrupted"`); `test_recover_pending_undo` (an op with `undo_state=pending` whose `src` is back on disk → undo `done`; otherwise `skipped` with `"interrupted"`).
- [ ] **Step 5: Run** — `PY -m pytest tests/core -v`. Expected: the new tests FAIL.
- [ ] **Step 6: Implement.** Each disk change is `record_pending` → `await asyncio.to_thread(op)` → `mark_done`/`mark_failed`. Files are processed sequentially (simple, journal order is obvious). Poster bytes are fetched before `record_pending`, so a network failure doesn't leave a pending op.
- [ ] **Step 7: Run** the tests. Expected: PASS.
- [ ] **Step 8: Commit** — `feat: journaled executor, safe fs ops, cleanup rules, crash recovery`

---

### Task 8: Undo service

**Files:**
- Create: `src/mediaforge/core/undo.py`, `tests/core/test_undo.py`

**Interfaces:**
- Consumes: `HistoryRepo`, `Operation`, `UndoState` (Task 5); `fsops` (Task 7).
- Produces: `class UndoResult(BaseModel)`: `undone: int`, `skipped: list[tuple[str, str]]` (path, reason); `class UndoService(history: HistoryRepo)`: `async undo_run(run_id: int) -> UndoResult`; `async undo_operations(op_ids: Sequence[int]) -> UndoResult`. Reason strings (exact): `"file was moved or renamed again"`, `"something else now has the old name"`, `"file has changed since"`, `"already gone"`, `"folder isn't empty"`, `"deleted junk can't be restored"`.

- [ ] **Step 1: Write the failing tests** (do a real `Executor` run in `tmp_path`, then undo):
  `test_undo_whole_run_restores_names_and_removes_created_dirs_and_posters`;
  `test_undo_selected_files_only` (only the chosen rename reverts; the run status becomes partly-undone);
  `test_undo_older_run_out_of_order` (two runs on different files; undo run 1 first → OK);
  `test_skip_when_moved_again`;
  `test_skip_when_old_name_taken`;
  `test_poster_changed_since_is_kept` (rewrite the poster with other bytes → skipped, `"file has changed since"`, file still there);
  `test_pruned_folder_recreated`;
  `test_undo_is_idempotent` (undoing twice → the second time `undone == 0`, nothing skipped twice);
  `test_create_dir_removed_last_and_only_if_empty`.
- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_undo.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Order: all non-`create_dir` ops by `(run_id desc, seq desc)`, then `create_dir` ops deepest first. Each undo is `mark_undo(PENDING)` → act → `mark_undo(DONE|SKIPPED, note)`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: selective, out-of-order undo with safety checks`

---

### Task 9: TMDB client

**Files:**
- Create: `src/mediaforge/models/tmdb.py`, `src/mediaforge/api/__init__.py`, `src/mediaforge/api/tmdb.py`, `tests/api/test_tmdb.py`

**Interfaces:**
- Consumes: `CacheRepo` (Task 4).
- Produces (`models/tmdb.py`): `TmdbResult(BaseModel)`: `id: int`, `title: str`, `original_title: str | None`, `date: str | None`, `overview: str = ""`, `poster_path: str | None`, `popularity: float = 0`; a `model_validator(mode="before")` maps `name→title`, `original_name→original_title`, `release_date|first_air_date→date`; extra fields are ignored; property `year -> int | None`. `TmdbEpisode`: `episode_number: int`, `name: str`. `TmdbSeason`: `season_number: int`, `poster_path: str | None`, `episodes: list[TmdbEpisode]`.
- Produces (`api/tmdb.py`): `class TmdbError(Exception)`; `class TmdbClient(session: aiohttp.ClientSession, api_key: str, *, language: str = "en-US", cache: CacheRepo | None = None, images_dir: Path | None = None, max_concurrency: int = 8)` with `BASE_URL = "https://api.themoviedb.org/3"`, `IMAGE_URL = "https://image.tmdb.org/t/p"`; `async validate_key() -> bool`; `async search_movie(query: str, year: int | None) -> list[TmdbResult]`; `async search_tv(query: str, year: int | None) -> list[TmdbResult]`; `async details(kind: MediaKind, tmdb_id: int) -> TmdbResult | None`; `async season(tv_id: int, number: int) -> TmdbSeason | None`; `async image(path: str, size: str) -> bytes`; `redact(text: str) -> str`.

- [ ] **Step 1: Write the failing tests** (`aioresponses` with regex URLs):
  `test_v3_key_sent_as_query` / `test_v4_token_sent_as_bearer` (a key starting with `eyJ`; no `api_key` param);
  `test_movie_search_with_year_then_without_dedupes_in_order`;
  `test_tv_search_uses_first_air_date_year`;
  `test_include_adult_false_and_language_sent`;
  `test_retry_on_429_then_success` (honour `Retry-After: 0`);
  `test_5xx_three_times_raises_tmdb_error`;
  `test_validate_key_false_on_401`;
  `test_season_404_returns_none`;
  `test_cache_hit_skips_network` (second call: no request);
  `test_cache_key_includes_language`;
  `test_image_cached_on_disk`;
  `test_key_never_in_exception_text` (a `TmdbError` message has `***` where the key was);
  `test_result_model_maps_tv_fields`.
- [ ] **Step 2: Run** — `PY -m pytest tests/api/test_tmdb.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Timeout `aiohttp.ClientTimeout(total=10)`, `asyncio.Semaphore(max_concurrency)`, retries 3 with a 0.5 s, 1 s, 2 s backoff (or `Retry-After`). Cache TTL is 7 days for JSON; images are files at `images_dir/size/<basename>`, valid for 30 days.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: async TMDB client with auth styles, retries, sqlite cache`

---

### Task 10: Matcher and resolver

**Files:**
- Create: `src/mediaforge/core/matcher.py`, `tests/core/test_matcher.py`

**Interfaces:**
- Consumes: `TmdbClient`, `TmdbResult` (Task 9); `MatchRepo`, `RememberedMatch` (Task 4); `ParsedMedia`, `ResolvedMedia`, `MatchState` (Tasks 2–3).
- Produces: `normalize_title(text: str) -> str`; `class Resolution(BaseModel, frozen)`: `media: ResolvedMedia`, `candidates: tuple[TmdbResult, ...]`; `choose(results: Sequence[TmdbResult], title: str, year: int | None) -> tuple[MatchState, TmdbResult | None]`; `class Resolver(tmdb: TmdbClient | None, matches: MatchRepo)` with `async resolve(parsed: ParsedMedia) -> Resolution` and `async apply_choice(parsed: ParsedMedia, chosen: TmdbResult | None, *, remember: bool = True) -> ResolvedMedia`; `def match_key(parsed: ParsedMedia) -> tuple[MediaKind, str, int | None]`.

- [ ] **Step 1: Write the failing tests:**
  `test_normalize` (`"Fast & Furious: 9!"` → `"fast and furious 9"`);
  `test_choose_confident_on_title_and_year`;
  `test_choose_confident_on_original_title`;
  `test_choose_single_result_confident`;
  `test_choose_uncertain_takes_top`;
  `test_choose_no_year_multiple_is_uncertain`;
  `test_resolve_remembered_wins` (with a fake TMDB whose search would return something else → state `REMEMBERED`);
  `test_resolve_remembered_skip_gives_skipped_offline_values`;
  `test_resolve_fetches_episode_title_and_season_poster` (season 1, ep 2 → `episode_title == "Theseus"`, `season_poster_path` set);
  `test_resolve_offline_without_client` (state `OFFLINE`, parsed values);
  `test_resolve_network_error_is_offline_not_crash` (the fake raises `TmdbError` → `OFFLINE`);
  `test_not_found`;
  `test_apply_choice_remembers`.
- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_matcher.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** spec §8.6. If the season lookup fails, keep the parsed episode title.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: match confidence rules and resolver with remembered choices`

---

### Task 11: Queue service

**Files:**
- Create: `src/mediaforge/core/queue.py`, `tests/core/test_queue.py`

**Interfaces:**
- Consumes: `parse_filename`, `is_video` (Task 2); `Resolver`, `Resolution`, `match_key` (Task 10); `PlanInput` (Task 6).
- Produces: `class ItemStatus(StrEnum)`: `LOOKING_UP, READY, NEEDS_CHOICE, WORKING, DONE, ERROR, SKIPPED`; `@dataclass class QueueItem`: `id: int`, `path: Path`, `dropped_folder: Path | None`, `parsed: ParsedMedia`, `resolution: Resolution | None`, `status: ItemStatus`, `message: str = ""`; properties `root` (`dropped_folder or path.parent`) and `media` (`resolution.media`, or `ResolvedMedia.offline(parsed)` while looking up); `class QueueEvent(BaseModel)`: `kind: Literal["added","updated","removed","cleared"]`, `ids: tuple[int, ...]`; `scan_paths(paths: Sequence[Path], recursive: bool) -> list[tuple[Path, Path | None]]` (sync; folders → its videos with the folder as `dropped_folder`; files → `(file, None)`); `class Queue(resolver: Resolver, on_change: Callable[[QueueEvent], None], lookup_concurrency: int = 6)` with `items: list[QueueItem]`, `hint: TypeHint`, `async add_paths(paths, recursive) -> int`, `remove(item_id)`, `clear()`, `async set_hint(hint: TypeHint)`, `async choose_match(item_id: int, chosen: TmdbResult | None)`, `uncertain() -> list[QueueItem]`, `plan_inputs(ids: Sequence[int] | None = None) -> list[PlanInput]`, `async wait_idle()`, `async rescan(recursive: bool)` (re-scans the remembered dropped folders and keeps loose files).

- [ ] **Step 1: Write the failing tests** (a fake resolver returning canned `Resolution`s):
  `test_add_folder_recursive_and_flat`;
  `test_non_videos_ignored`;
  **Review focus 5:** `test_same_file_added_twice_appears_once_and_folder_root_wins` (add the file alone, then its folder → one item, `dropped_folder` set);
  `test_lookups_set_ready_or_needs_choice`;
  `test_choose_match_applies_to_same_key` (two episodes of one show both turn READY after one choice);
  `test_set_hint_reparses_all`;
  `test_events_emitted`;
  `test_remove_and_clear`;
  `test_rescan_after_toggle`;
  `test_lookup_concurrency_limited` (the fake records the max in flight, which is ≤ 6);
  `test_scan_runs_off_loop` (`scan_paths` is called through `asyncio.to_thread`, checked by monkeypatching `asyncio.to_thread`).
- [ ] **Step 2: Run** — `PY -m pytest tests/core/test_queue.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Dedupe on `os.path.normcase(str(path.resolve()))`. Lookups are `asyncio.Task`s kept in a set and cancelled on `remove`/`clear`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: queue service with background lookups and shared match choices`

---

### Task 12: Trailer tools and downloads

**Files:**
- Create: `src/mediaforge/api/trailers.py`, `tests/api/test_trailers.py`, `tests/api/fake_ytdlp.py`

**Interfaces:**
- Consumes: `PlannedTrailer` (Task 6); `SettingsRepo` (Task 4).
- Produces: constants `YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"`, `YTDLP_SUMS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"`, `FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"`, `FFMPEG_SUMS_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/checksums.sha256"`, `FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"`, `UPDATE_INTERVAL_S = 7 * 24 * 3600`; `youtube_search_url(title: str, year: int | None) -> str`; `sha256_for(sums_text: str, filename: str) -> str | None`; `class TrailerService(tools_dir: Path, session: aiohttp.ClientSession, settings: SettingsRepo, *, ytdlp_command: Sequence[str] | None = None)` with `tools_present() -> bool`, `async install_tools(on_progress: Callable[[str, float], None]) -> None` (raises `ToolInstallError`), `async maybe_update() -> None`, `async download(trailer: PlannedTrailer) -> Path | None` (satisfies the `TrailerDownloader` protocol from Task 7).

- [ ] **Step 1: Write the failing tests.** `fake_ytdlp.py` is a script that writes `<stem>.mp4` from its `-o` argument, or exits 1 when the env var `FAKE_FAIL=1` is set. Inject it with `ytdlp_command=[sys.executable, fake_path]`.
  `test_download_success_returns_mp4`;
  `test_download_failure_writes_url_shortcut` (the content contains `URL=https://www.youtube.com/results?search_query=Dune+Part+Two+2024+official+trailer`);
  `test_existing_trailer_is_not_redownloaded` (returns None);
  `test_command_has_no_shell_and_expected_args` (capture argv: `-f FORMAT`, `--merge-output-format mp4`, `--ffmpeg-location <tools/ffmpeg/bin>` only when present, `-o <stem>.%(ext)s`, then the query last);
  `test_install_verifies_checksum` (aioresponses serves bytes plus a sums file; a wrong hash → `ToolInstallError` and no file left behind);
  `test_install_extracts_only_bin` (a tiny zip containing `ffmpeg-…/bin/ffmpeg.exe`, `…/doc/x` → only `tools/ffmpeg/bin/ffmpeg.exe` exists);
  `test_maybe_update_respects_interval`;
  `test_sha256_for_parses_sums`.
- [ ] **Step 2: Run** — `PY -m pytest tests/api/test_trailers.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Download to `*.part`, verify, then rename into place. Run with `asyncio.create_subprocess_exec` and `creationflags=subprocess.CREATE_NO_WINDOW`. After one failed download, run `yt-dlp -U` and retry once.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: trailer downloads via verified yt-dlp/ffmpeg tools`

---

### Task 13: Logging and legacy import

**Files:**
- Create: `src/mediaforge/logging_setup.py`, `src/mediaforge/db/legacy_import.py`, `tests/test_logging_setup.py`, `tests/db/test_legacy_import.py`, `tests/db/fixtures/legacy_config.json`, `tests/db/fixtures/legacy_undo.json`

**Interfaces:**
- Produces (`logging_setup.py`): `class LogLine(BaseModel)`: `level: int`, `text: str`, `success: bool`; `class UiLogBridge(logging.Handler)` with `drain(max_items: int = 200) -> list[LogLine]` (thread-safe queue); `configure_logging(logs_dir: Path) -> UiLogBridge` (rotating file, 1 MB × 5, plus the bridge, plus `RedactingFilter`); `set_secret(value: str) -> None`; `log_success(logger: logging.Logger, msg: str) -> None`.
- Produces (`db/legacy_import.py`): `class ImportReport(BaseModel)`: `presets: int`, `key_imported: bool`, `history_files: int`; `find_legacy(dirs: Sequence[Path]) -> tuple[Path | None, Path | None]`; `async import_legacy(config: Path | None, undo: Path | None, settings: SettingsRepo, presets: PresetRepo, history: HistoryRepo) -> ImportReport`.

- [ ] **Step 1: Write the fixtures.** `legacy_config.json` copies the structure of the owner's file with `"TMDB_API_KEY": "dummy-key-0000"`, the five presets and `"window_geometry": "1200x850+38+38"`. `legacy_undo.json` holds two renames with the **file names** from the owner's undo file under a `tmp_path` placeholder `{ROOT}` (substituted in the test), and `"created": []`. **Never copy the real key.**
- [ ] **Step 2: Write the failing tests:**
  `test_secret_redacted_in_file_and_ui` (log `"url ...api_key=SECRET..."` after `set_secret("SECRET")`; both outputs contain `***`);
  `test_success_flag`;
  `test_drain_from_other_thread`;
  `test_import_presets_options_mapping` (`Series (Folder)` gets `series_poster` and `season_poster` True; `chk_recursive` is ignored);
  `test_import_sets_key_theme_geometry_and_flag`;
  `test_import_undo_becomes_one_run` (`source == RunSource.IMPORT`, 2 renames, undoable);
  `test_import_is_one_time` (a second call when `legacy_imported` is True does nothing);
  `test_legacy_files_untouched` (bytes are the same before and after).
- [ ] **Step 3: Run** — `PY -m pytest tests/test_logging_setup.py tests/db/test_legacy_import.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** both modules.
- [ ] **Step 5: Run** the tests. Expected: PASS.
- [ ] **Step 6: Commit** — `feat: logging with UI bridge and redaction; one-time legacy import`

---

### Task 14: Windows integration (CLI, single instance, context menu)

**Files:**
- Create: `src/mediaforge/platform/cli.py`, `src/mediaforge/platform/single_instance.py`, `src/mediaforge/platform/context_menu.py`, `tests/platform/test_cli.py`, `tests/platform/test_single_instance.py`, `tests/platform/test_context_menu.py`

**Interfaces:**
- Produces (`cli.py`): `class LaunchArgs(BaseModel)`: `paths: list[Path] = []`, `preset: str | None = None`, `confirm: bool = False`, `self_test: bool = False`; `parse_args(argv: Sequence[str]) -> LaunchArgs` (`--auto-run` means `confirm=True`; `--self-test` sets `self_test`).
- Produces (`single_instance.py`): `class InstanceMessage(BaseModel)`: `paths: list[str]`, `preset: str | None`, `confirm: bool`; `pipe_address() -> str` (`\\.\pipe\MediaForge-<username>`); `class Primary` with `start(on_message: Callable[[InstanceMessage], None]) -> None` (a listener thread using `multiprocessing.connection.Listener(address, family="AF_PIPE", authkey=key)`) and `close()`; `try_become_primary(key_file: Path, *, name: str | None = None) -> Primary | None` (a named mutex `Local\MediaForge-<username>` via `ctypes`: `CreateMutexW` + `GetLastError() == 183` → None); `send_to_primary(msg: InstanceMessage, key_file: Path, timeout_s: float = 5.0) -> bool` (retries until timeout); `class ConfirmBatcher(quiet_s: float = 0.4, on_batch: Callable[[str | None, list[Path]], None])` with `add(msg: InstanceMessage)`, used from the Tk loop via `root.after`.
- Produces (`context_menu.py`): `@dataclass(frozen=True) class RegEntry`: `key: str`, `values: dict[str, str]` (`""` = default value); `BASES = (r"Software\Classes\*\shell\MediaForge", r"Software\Classes\Directory\shell\MediaForge")`; `LEGACY_HKLM_KEYS = (r"SOFTWARE\Classes\*\shell\MediaForgeCommands", r"SOFTWARE\Classes\Folder\shell\MediaForgeCommands")`; `command_prefix() -> list[str]` (frozen: `[sys.executable]`; source: `[<pythonw.exe>, "-m", "mediaforge"]`); `build_entries(presets: Sequence[str], prefix: Sequence[str], icon: str) -> list[RegEntry]`; `to_reg_file(entries: Sequence[RegEntry], hive: str = "HKEY_CURRENT_USER") -> str`; `install(entries)`; `uninstall()`; `installed_command() -> str | None`; `legacy_installed() -> bool`; `legacy_removal_reg() -> str`.

- [ ] **Step 1: Write the failing tests:**
  `test_parse_paths_preset_confirm`;
  `test_auto_run_means_confirm`;
  `test_build_entries_structure`: the base has `MUIVerb="MediaForge"`, `SubCommands=""`, `Icon`; the item key `…\shell\01_SeriesFolder` has `MUIVerb="Forge: Series (Folder)"` and `MultiSelectModel="Player"`; the command default is `"<exe>" --confirm --preset "Series (Folder)" "%1"`; the order is kept through the `NN_` prefixes;
  `test_reg_file_escaping` (backslashes doubled, quotes escaped, header `Windows Registry Editor Version 5.00`);
  `test_legacy_removal_reg_uses_minus_keys`;
  `test_mutex_second_is_secondary` (two `try_become_primary` calls with a unique test `name` → the second is None);
  `test_send_and_receive_over_pipe` (a primary on a unique name receives the message);
  `test_batcher_groups_by_preset_after_quiet_period` (drive time by calling `flush_due(now)`).
- [ ] **Step 2: Run** — `PY -m pytest tests/platform -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Use `winreg` only inside `install`/`uninstall`/`installed_command`/`legacy_installed`; tests never write to the registry. Create the auth key file (32 random bytes) on first use.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: CLI args, single-instance pipe, HKCU context menu builder`

---

### Task 15: App composition root and Studio shell

**Files:**
- Create: `src/mediaforge/app.py`, `src/mediaforge/__main__.py`, `src/mediaforge/ui/__init__.py`, `ui/theme.py`, `ui/dnd_root.py`, `ui/main_window.py`, `ui/log_strip.py`, `tests/ui/conftest.py`, `tests/ui/test_shell.py`
- Copy: `MediaForge.ico`, `MediaForge.png` → `src/mediaforge/assets/` (package data)

**Interfaces:**
- Consumes: everything in Tasks 1–14.
- Produces (`app.py`): `@dataclass class Services`: `paths`, `db`, `settings: SettingsRepo`, `presets: PresetRepo`, `matches: MatchRepo`, `cache: CacheRepo`, `history: HistoryRepo`, `session: aiohttp.ClientSession`, `tmdb: TmdbClient | None`, `resolver: Resolver`, `queue: Queue`, `executor: Executor`, `undo: UndoService`, `trailers: TrailerService`, `log_bridge: UiLogBridge`, `app_settings: AppSettings`; `async build_services(paths: AppPaths, legacy_dirs: Sequence[Path]) -> Services` (opens the DB, runs `recover`, seeds presets, auto-imports legacy JSON found by `find_legacy(legacy_dirs)` (the exe's folder and the current folder), calls `set_secret(tmdb_key)`, configures logging); `async rebuild_tmdb(services: Services) -> None` (after a key or language change); `async run(args: LaunchArgs, primary: Primary) -> None`; `async self_test() -> int`.
- Produces (`__main__.py`): `main(argv: Sequence[str] | None = None) -> int`. Order: parse args; `--self-test` → `asyncio.run(self_test())`; else `try_become_primary`; if secondary, `send_to_primary` and return 0; else `asyncio.run(run(args, primary))`.
- Produces (`ui/theme.py`): `@dataclass(frozen=True) class Palette` with fields `main, rail, surface, raised, border, text, muted, accent, accent_text, success, error, warning`; `STUDIO_DARK` (values from Global Constraints, `accent_text="#16181d"`, `warning="#f2a33a"`), `STUDIO_LIGHT` (main `#f6f7f9`, rail `#eceef2`, surface `#ffffff`, raised `#e9ebf0`, border `#d8dbe2`, text `#1b1f2a`, muted `#5c6473`, accent `#d9861c`, accent_text `#ffffff`, success `#2e9e6a`, error `#c9423a`, warning `#d9861c`); `palette_for(theme: str) -> Palette`; `FONT_FAMILY = "Segoe UI"`, `MONO_FAMILY = "Consolas"`.
- Produces (`ui/main_window.py`): `class MainWindow(DnDCTk)`: `__init__(services: Services, palette: Palette)`; `show_view(name: Literal["queue","history","settings"]) -> None`; `views: dict[str, ctk.CTkFrame]`; restores and saves `window_geometry`; binds drops to `services.queue.add_paths`.
- Produces (`ui/log_strip.py`): `class LogStrip(ctk.CTkFrame)`: polls `log_bridge.drain()` every 150 ms via `after`, colours by level/success, collapsible.

- [ ] **Step 1: Write the failing UI tests** (marked `ui`; the fixture builds `Services` with `MEDIAFORGE_HOME` and no TMDB key, creates a `MainWindow` at off-screen geometry `+4000+4000`, and pumps with `root.update()`):
  `test_shell_builds_and_switches_views` (each `show_view` raises the right frame);
  `test_rail_highlight_follows_view`;
  `test_log_strip_shows_records` (`logging.getLogger("mediaforge").error("boom")`, pump → the text appears);
  `test_geometry_saved_on_close`;
  `test_self_test_returns_zero` (`asyncio.run(self_test()) == 0`: it parses a known name, opens a temp DB and renders a preset).
- [ ] **Step 2: Run** — `PY -m pytest tests/ui/test_shell.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the shell to match the mockup `.superpowers/brainstorm/543-1792933811/content/main-window-looks.html`, option A (rail 52 px: logo square, then ☰ Queue, ⟲ History, ⚙ Settings). In `run()`, on the first start show `FirstStart` (Task 18) before the main window when `not app_settings.first_start_done`. Install `loop.set_exception_handler(...)` so that an unhandled exception in any task is logged with its traceback to the file, with a one-line summary in the log strip. Clean up `session.close()` and `db.close()` in `finally`.
- [ ] **Step 4: Run** — the UI tests pass and `PY -m mediaforge` opens the window. Close it by hand and check `%LOCALAPPDATA%\MediaForge\logs\mediaforge.log` exists. (When run from this tool shell, `%LOCALAPPDATA%` may be virtualized; set `MEDIAFORGE_HOME` to a scratch dir for the manual check.)
- [ ] **Step 5: Commit** — `feat: composition root, studio theme and window shell`

---

### Task 16: Queue view, detail panel, match picker

**Files:**
- Create: `src/mediaforge/ui/widgets/__init__.py`, `ui/widgets/virtual_list.py`, `ui/widgets/chips.py`, `ui/queue_view.py`, `ui/detail_panel.py`, `ui/match_picker.py`, `tests/ui/test_queue_view.py`, `tests/ui/test_virtual_list.py`

**Interfaces:**
- Consumes: `Queue`, `QueueItem`, `QueueEvent` (Task 11); `build_plan`, `describe` (Task 6); `Executor`, `ProgressEvent` (Task 7); `PresetRepo` (Task 4); `render` (Task 3); `Palette` (Task 15).
- Produces: `class Row(BaseModel)`: `key: int`, `kind: Literal["group","item"]`, `title: str`, `subtitle: str = ""`, `badge: str = ""`, `status: str = ""`, `highlight: tuple[int, int] | None = None` (span of `SxxEyy` in the title); `class VirtualList(ctk.CTkFrame)`: `__init__(master, palette, row_height=46, group_height=26)`, `set_rows(rows: Sequence[Row])`, `select(key: int | None)`, `on_select: Callable[[int | None], None]`, `on_remove: Callable[[int], None]`, `visible_keys() -> list[int]` (it draws only visible rows on a `tk.Canvas`); `class QueueView(ctk.CTkFrame)`: `__init__(master, services, palette)`, `async forge()`, `async preview()`, `refresh()`; `class DetailPanel(ctk.CTkFrame)`: `show(item: QueueItem | None)`; `class MatchPicker(ctk.CTkToplevel)`: `async ask(master, item: QueueItem, services) -> TmdbResult | None | Literal["cancel"]` (None = skip TMDB).

  Behaviour:
  - An empty queue shows a drag-and-drop hint plus **Add folder** / **Add files** buttons. The same two actions stay available in the top bar once files are queued.
  - Rows group by destination: the folder from `render().parts[:-1]`, or `"Series (Year)"` / `"Flat"`, as in the legacy app.
  - Changing the format updates rows within about 150 ms (debounced).
  - The preview line under the format shows the selected item's path and flags unknown `{vars}`.
  - Chips toggle `PresetOptions`; Scan subfolders saves to settings and calls `queue.rescan`.
  - Forge with uncertain items shows the three-choice prompt (§6.2).
  - Forge disables the buttons, shows progress, and offers Cancel.
  - Keys: `Enter` = preview, `Ctrl+Enter` = forge, `Ctrl+Z` = undo the latest run (with confirm), `Up`/`Down` = select. All of them are ignored while an Entry or Text has focus.
  - The detail panel loads the `w342` poster through `tmdb.image` in a task. "Change match…" opens the `MatchPicker` (8 candidates, `w92` thumbnails, Use this / Skip TMDB for this file / Use top result). "Watch trailer" opens `youtube_search_url(...)` with `webbrowser.open`.
  - First trailer use with tools missing asks once, then `trailers.install_tools`.

- [ ] **Step 1: Write the failing tests:**
  `test_virtual_list_draws_only_visible` (1,000 rows → `len(visible_keys()) < 40`; `set_rows` < 200 ms);
  `test_virtual_list_scroll_changes_visible`;
  `test_queue_view_lists_added_files` (create 5 fake `.mkv` in `tmp_path`, add, pump until the queue is idle → 5 item rows plus group rows);
  `test_format_change_updates_titles`;
  `test_forge_renames_and_shows_done` (offline, Flat preset → files renamed on disk, rows show done, one History run exists);
  `test_preview_writes_plan_to_log_and_touches_nothing`;
  `test_shortcuts_ignored_in_entry`;
  `test_responsive_with_1000_items` (Review: adding 1,000 fake files keeps each `root.update()` under 100 ms).
- [ ] **Step 2: Run** — `PY -m pytest tests/ui/test_queue_view.py tests/ui/test_virtual_list.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** to match mockup option A. Wire it into `MainWindow.views["queue"]`.
- [ ] **Step 4: Run** the tests, then the manual check: `PY -m mediaforge <folder of fake .mkv files>` shows grouped rows, the detail panel and working chips.
- [ ] **Step 5: Commit** — `feat: studio queue view with virtual list, detail panel, match picker`

---

### Task 17: History view

**Files:**
- Create: `src/mediaforge/ui/history_view.py`, `tests/ui/test_history_view.py`

**Interfaces:**
- Consumes: `HistoryRepo`, `RunSummary`, `Operation` (Task 5); `UndoService`, `UndoResult` (Task 8).
- Produces: `class HistoryView(ctk.CTkFrame)`: `__init__(master, services, palette)`, `async reload()`, `async undo_run(run_id)`, `async undo_selected(run_id)`. It reloads whenever it's shown and after every Forge.

  Layout per mockup `history-and-confirm.html` §1: the run card shows when (`Today 14:02` / `Yesterday 21:40` / `19 Oct 22:23`), `N files · preset · extras`, the source tag (`right-click`, `imported`), **Show files** (expand), and **Undo run**. Expanded, each op has a tick box (non-undoable ops are shown disabled with the reason) and **Undo N selected**. A fully undone run is dimmed with `Undone <date>`. Undo asks for confirmation ("This will put back N files and remove M downloaded files. Continue?") and then shows a summary listing skipped items with reasons.

- [ ] **Step 1: Write the failing tests:**
  `test_lists_runs_newest_first`;
  `test_undo_run_button_reverts_files` (a real run via Executor in `tmp_path`, click with the confirm auto-accepted through an injectable `confirm` callable);
  `test_undo_selected_only`;
  `test_undone_run_dimmed_with_tag`;
  `test_when_labels` (pure helper `when_label(dt, now) -> str`, unit-tested).
- [ ] **Step 2: Run** — `PY -m pytest tests/ui/test_history_view.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: history view with whole-run and per-file undo`

---

### Task 18: Settings view and first start

**Files:**
- Create: `src/mediaforge/ui/settings_view.py`, `src/mediaforge/ui/first_start.py`, `tests/ui/test_settings_view.py`

**Interfaces:**
- Consumes: `SettingsRepo`, `PresetRepo`, `MatchRepo` (Task 4); `TmdbClient.validate_key` (Task 9); `context_menu` (Task 14); `find_legacy`, `import_legacy` (Task 13); `rebuild_tmdb` (Task 15).
- Produces: `class SettingsView(ctk.CTkFrame)`: sections Appearance, TMDB, Presets, Right-click menu, Remembered matches, About (spec §6.5). `class FirstStart(ctk.CTkToplevel)`: `async run(services) -> None` (spec §6.6 steps 1–3; the TMDB link is `https://www.themoviedb.org/settings/api`).

  Behaviour: The TMDB section has the key field and a result-language field (default `en-US`). Check key → `validate_key` → green "Key works" or red "TMDB rejected this key". Saving → `set_secret(key)` + `rebuild_tmdb`. Theme change applies the palette on restart (say so). Presets: list + editor (name, type segmented auto/movie/tv, format with live sample, six option checkboxes) + Save / Delete (`LastPresetError` → message). Right-click: preset checklist, **Add to right-click menu** / **Remove**, **Save as .reg file…**; shows "Menu points to an old location · Fix" when `installed_command()` differs from `command_prefix()`; shows "Old MediaForge menu found · Save removal file…" when `legacy_installed()`. Remembered matches: a list with Forget / Clear all. About: version, "Open log folder".

- [ ] **Step 1: Write the failing tests:**
  `test_save_preset_updates_queue_dropdown`;
  `test_delete_last_preset_shows_error`;
  `test_key_check_uses_validate` (fake TMDB);
  `test_context_menu_buttons_call_install_with_entries` (monkeypatch `context_menu.install`);
  `test_stale_menu_detected`;
  `test_first_start_imports_legacy_and_marks_done` (fixture JSON next to a fake exe dir passed to `find_legacy`).
- [ ] **Step 2: Run** — `PY -m pytest tests/ui/test_settings_view.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: settings view and first-start setup`

---

### Task 19: Confirm window and the right-click flow

**Files:**
- Create: `src/mediaforge/ui/confirm_window.py`, `tests/ui/test_confirm_window.py`
- Modify: `src/mediaforge/app.py` (route `--confirm` launches and pipe messages to confirm windows through `ConfirmBatcher`; a non-confirm message brings the main window to the front, creating it if only confirm windows were open, and queues its paths with its preset selected)

**Interfaces:**
- Consumes: `Queue` (a **separate** `Queue` instance per confirm window), `build_plan`, `Executor`, `UndoService`, `MatchPicker`, `ConfirmBatcher`.
- Produces: `class ConfirmWindow(ctk.CTkToplevel)`: `__init__(master, services, palette, paths: list[Path], preset_name: str | None)`; `async rename()`; `open_in_main()`. Run source = `RunSource.RIGHT_CLICK`.

  Layout per mockup `history-and-confirm.html` §2: "Rename N files?", the preset dropdown, "Nothing changes until you click Rename", rows with a tick box plus new over old, uncertain rows highlighted with the inline "Change…", and a footer with `x of N selected` / Open in MediaForge / Cancel / **Rename x files**. Afterwards: "4 renamed, 1 skipped" with **Undo** / **Close**. When the app was launched only for `--confirm` and the last window closes, the process exits.

- [ ] **Step 1: Write the failing tests:**
  `test_lists_files_with_new_names`;
  `test_unticked_files_untouched`;
  `test_rename_creates_right_click_run`;
  `test_undo_button_reverts`;
  `test_open_in_main_moves_items_to_main_queue`;
  `test_batcher_to_window_one_window_for_many_paths` (feed 12 `InstanceMessage`s for one preset → one window with 12 rows);
  `test_nothing_happens_on_cancel`;
  `test_plain_launch_message_raises_main_window_and_queues_paths`.
- [ ] **Step 2: Run** — `PY -m pytest tests/ui/test_confirm_window.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests, then the manual check: install the context menu from Settings (source mode), select 3 fake files in Explorer → Show more options → MediaForge → Forge: Series (Flat) → **one** confirm window with 3 rows.
- [ ] **Step 5: Commit** — `feat: right-click confirm window with batching and undo`

---

### Task 20: Packaging and release zip

**Files:**
- Create: `packaging/mediaforge.spec`, `packaging/build.py`, `packaging/collect_licenses.py`, `tests/test_packaging.py`
- Modify: `CLAUDE.md` (a "Build" section), `.gitignore` (already ignores `build/`, `dist/`)

**Interfaces:**
- Consumes: `main` (Task 15), `--self-test`.
- Produces: `PY packaging/build.py` → `dist/MediaForge-1.0.0-win64.zip`, containing the `MediaForge/` folder with `MediaForge.exe`, `licenses/`, and `README.txt` (5 lines: unzip anywhere, run MediaForge.exe, get a TMDB key, right-click setup in Settings, where data lives).

- [ ] **Step 1: Write the failing test** `test_collect_licenses_finds_runtime_deps` (`collect_licenses.py` returns license texts for customtkinter, tkinterdnd2, aiohttp, pydantic and guessit).
- [ ] **Step 2: Write the spec file.** One-folder, windowed, `icon=MediaForge.ico`, name `MediaForge`. `collect_data_files("customtkinter")`, `collect_data_files("guessit")`, `collect_data_files("babelfish")`, `collect_data_files("tkinterdnd2")` + `collect_dynamic_libs("tkinterdnd2")`, package data `mediaforge/assets/*`. `module_collection_mode={"guessit": "py", "rebulk": "py"}`. Exclude `pytest`, `aioresponses`, `pyright`.
- [ ] **Step 3: Write `build.py`.** It runs PyInstaller with `--workpath` and `--distpath` under `%LOCALAPPDATA%\MediaForge-dev\build` (outside Google Drive), copies the licenses and README, zips the result to `<project>/dist/MediaForge-<version>-win64.zip`, then runs `MediaForge.exe --self-test` from the unzipped folder and fails the build when the exit code isn't 0.
- [ ] **Step 4: Run** — `PY -m pytest tests/test_packaging.py -v`, then `PY packaging/build.py`. Expected: the tests pass, the build prints `self-test OK`, and the zip exists (about 40–70 MB).
- [ ] **Step 5: Manual check.** Unzip to a fresh folder, then launch `MediaForge.exe` with `MEDIAFORGE_HOME` set to a scratch dir. The first-start screen appears, and dropping a folder of fake files and forging works.
- [ ] **Step 6: Commit** — `chore: PyInstaller one-folder build, licenses, release zip`

---

### Task 21: End-to-end verification against the legacy checklist

**Files:**
- Create: `tests/test_legacy_parity.py`, `docs/superpowers/verification/2026-10-26-legacy-parity.md`

- [ ] **Step 1: Write the parity tests.** For each of the 23 items in spec Appendix A, add one test, or a reference to the existing test that covers it, as a table in the verification doc. Items needing a human (drag and drop from Explorer, Explorer menu, look and feel) get a manual check line.
- [ ] **Step 2: Run the full suite** — `PY -m pytest -v`, `PY -m ruff check src tests`, `PY -m pyright`. Expected: all green, zero errors.
- [ ] **Step 3: Do the manual checks** listed in the doc, using the built exe, on a copy of real-looking files (not the owner's NAS). Record pass/fail.
- [ ] **Step 4: Commit** — `test: legacy parity checklist and verification record`
