# MediaForge 1.2: AirGrabber's Family Look — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MediaForge 1.2.0 looks like AirGrabber's sibling: AirGrabber's colours and accents, a
top band with a Series | Movies switch, an Inbox of show and movie bands in place of the Queue,
History in bands grouped by day, and Settings in tabs — with every 1.1 feature still reachable.

**Architecture:** Views keep taking a `Palette`; the palettes are rebuilt from AirGrabber's tokens
and accent palettes, so every existing widget changes colour without being rewritten. The icon
rail becomes a top band in `MainShell`. The fast canvas list gains band rows (backdrop, poster,
title, actions) that the Inbox builds from the queue; History keeps its run cards, restyled as
bands under day headings; Settings moves its sections into tabs.

**Tech Stack:** Python 3.13, customtkinter 6, Pillow, aiohttp (TMDB images), pydantic.

**Spec:** `docs/superpowers/specs/2026-10-31-family-look-and-automatic-design.md` (this plan is
stage 1 only; stage 2 — watching, tray, automatic renaming — gets its own plan).

**Source to port:** the AirGrabber project folder: `app/ui/theme.py` (tokens), `app/ui/palettes.py`
(accent palettes, `tokens()`), `app/models/settings.py` (`Accent`). Read before porting.

## Global Constraints

- Everything in `CLAUDE.md` holds: one asyncio loop, `gc_guard`, parsing on the loop, long paths,
  `prepare_popup`, light imports for secondary processes, `window.update()` before the loop.
- Colours: AirGrabber's Slate palette is the default; dark values from AirGrabber's dark tokens,
  light from its light tokens; the 10 accents of `Accent` (slate, ocean, petrol, teal, sage,
  indigo, plum, rose, mocha, graphite). Light/dark/follow-Windows is chosen at start; a change takes
  a restart (as 1.x and as AirGrabber's accent).
- Nothing 1.1 can do is lost (spec anti-goal list): presets and editor, format entry and sample,
  option chips, Preview/Forge/Cancel/Undo latest, drag and drop, + Files/+ Folder/Clear, match
  picker, detail panel, keyboard shortcuts, right-click confirm window, History undo per run and
  per file, Settings sections, update checks.
- Thousands of lines stay smooth: the Inbox list stays canvas-drawn; the 1,000-item
  responsiveness test keeps passing.
- Internal names stay (`QueueView`, view key `"queue"`) so 1.x code and tests keep working; only
  what the owner sees is renamed ("Inbox").
- Version `1.2.0`. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`;
  `user.email` is `beau_spiessens@hotmail.com`. Never push without the owner's OK.

## Review Focus

1. **Light mode and every accent:** text must stay readable on every surface (bands over
   backdrops, chips, buttons) in light and dark and all 10 accents — check contrast, not just dark
   Slate (Task 1 test).
2. **Bands with missing images:** no TMDB key, offline, no backdrop or poster for a title: bands
   still draw (a tinted fill and initials), never an error or an empty hole (Task 4).
3. **A thousand files in bands:** scrolling and refreshing stay as quick as 1.1's list; images load
   lazily for visible bands only (Task 4).
4. **Mixed shows and movies dropped together:** each lands on its own side of the switch with the
   right count; switching never loses a selection's action (Forge acts on all pending, both
   kinds, as today) (Task 5).
5. **Small windows and 150 % scaling:** the top band, Inbox header and bottom bar don't push
   buttons off-screen at the minimum window size (1000×640) (Tasks 2 and 5).

---

### Task 1: AirGrabber's colours and accents

**Files:**
- Modify: `src/mediaforge/ui/theme.py`, `src/mediaforge/db/settings.py` (`accent: str = "slate"`;
  `theme` keeps "Dark" | "Light" | "System")
- Create: `src/mediaforge/ui/palettes.py` (port)
- Modify (rename only): every `STUDIO_DARK` / `STUDIO_LIGHT` use in `src` and `tests` → `DARK` /
  `LIGHT`
- Test: `tests/ui/test_theme.py`

**Interfaces:**
- Produces: `Palette` (same fields as now: `name, main, rail, surface, raised, border, text, muted,
  accent, accent_text, success, error, warning`) plus new fields `chip: str`, `selected: str`,
  `link: str`; `ACCENTS: tuple[str, ...]`; `palette_for(theme: str, accent: str = "slate") ->
  Palette`; `DARK`, `LIGHT` (Slate). Field mapping from AirGrabber tokens (dark/light value):
  `main=BG, rail=TAB_BG, surface=SURFACE, raised=CHIP, border=EDGE(dark: PLACEHOLDER-like quiet
  line, see palettes), text=TEXT, muted=TEXT_MUTED, accent=ACCENT, accent_text=ON_ACCENT,
  success=SUCCESS, error=DANGER, warning=WARNING, chip=CHIP, selected=SELECTED, link=LINK`.

- [ ] **Step 1: Failing tests:** `test_slate_dark_matches_airgrabber` (`DARK.main == "#161616"`,
  `DARK.accent == "#4E6A85"`, `LIGHT.main == "#F2F4F7"`), `test_every_accent_builds_both_looks`
  (all 10 × light/dark), `test_text_is_readable_everywhere` (WCAG contrast ≥ 4.5 for `text` on
  `main`/`surface`/`raised`, ≥ 3 for `muted` and for `accent_text` on `accent`, every accent and
  look), `test_unknown_accent_falls_back_to_slate`.
- [ ] **Step 2: Run** `PY -m pytest tests/ui/test_theme.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the port; rename `STUDIO_*` everywhere (a sed over `src` and `tests`).
- [ ] **Step 4: Run** the full suite — Expected: all pass (colour-asserting tests updated to the new
  values only where they compared literal colours).
- [ ] **Step 5: Commit** `feat: AirGrabber's colours and accent palettes`.

### Task 2: The top band

**Files:**
- Modify: `src/mediaforge/ui/main_window.py` (`MainShell`), `src/mediaforge/ui/queue_view.py`,
  `src/mediaforge/ui/history_view.py`, `src/mediaforge/ui/settings_view.py` (each gains
  `set_kind`)
- Test: `tests/ui/test_shell.py`

**Interfaces:**
- Produces: `MainShell.kind: Literal["series", "movies"]` (default "series"), `MainShell.set_kind(kind)`
  (calls `set_kind` on every view that has it), `MainShell.page_buttons: dict[str, ctk.CTkButton]`
  (replaces `rail_buttons`), `MainShell.kind_switch: ctk.CTkSegmentedButton` (values "Series",
  "Movies", texts may carry a count: "Movies · 2"), `MainShell.set_counts(needs: int, other_kind: int)`,
  `MainShell.status_label`. Grid: band row 0, content row 1, update bar row 2, log strip row 3.

Band, left to right: logo (`assets/MediaForge.png`, 22 px) and "MediaForge" (bold 14), the kind
switch, the pages Inbox (with a red count badge when `needs > 0`) · History · Settings as flat
buttons (the current page in `text` with an accent underline, the others `muted`), and on the right
`status_label` ("Drop files here" in 1.2). Fill `palette.rail`, a 1 px `palette.border` line under it.

- [ ] **Step 1: Failing tests** (replace the rail tests): `test_band_shows_pages_and_marks_the_current`,
  `test_kind_switch_reaches_every_view` (fake views record `set_kind`), `test_inbox_badge_shows_needs_count`,
  `test_band_fits_the_minimum_window` (root at 1000×640: every page button and the switch are mapped
  and inside the window).
- [ ] **Step 2–4:** run `PY -m pytest tests/ui/test_shell.py -q` FAIL → implement → PASS, then the
  full suite.
- [ ] **Step 5: Commit** `feat: AirGrabber-style top band with a Series | Movies switch`.

### Task 3: Backdrops from TMDB

**Files:**
- Modify: `src/mediaforge/models/tmdb.py` (`backdrop_path: str | None = None` on search/detail
  results), `src/mediaforge/models/media.py` (`ResolvedMedia.backdrop_path`), `src/mediaforge/core/matcher.py`
  (carry it, including remembered matches), `src/mediaforge/db/matches.py` if it stores the chosen
  result
- Create: `src/mediaforge/ui/images.py` — `ImageCache(services)`: `async poster(path, w, h)`,
  `async backdrop(path, w, h, fade_to: str)` (Pillow: crop to cover, darken and fade right-to-left into
  `fade_to`), returning `ImageTk.PhotoImage` kept alive in an LRU (≤ 200), made on the loop thread
- Test: `tests/core/test_matcher.py`, `tests/ui/test_images.py`

- [ ] **Step 1: Failing tests:** `test_backdrop_comes_with_the_match` (fake TMDB result with a backdrop
  → `ResolvedMedia.backdrop_path`), `test_remembered_match_keeps_its_backdrop`,
  `test_backdrop_fades_into_the_window_colour` (right-most column ≈ `fade_to`),
  `test_missing_image_gives_none` (TMDB 404 / no key → `None`, no exception).
- [ ] **Step 2–4:** FAIL → implement (TMDB size `w780` for backdrops, `w185` for band posters, the
  existing disk cache) → PASS.
- [ ] **Step 5: Commit** `feat: show backdrops from TMDB`.

### Task 4: Band rows in the fast list

**Files:**
- Modify: `src/mediaforge/ui/widgets/virtual_list.py`
- Test: `tests/ui/test_virtual_list.py`

**Interfaces:**
- Consumes: Task 3's `ImageCache` through a callback.
- Produces: `Row.kind` gains `"section"` (small upper-case heading, e.g. "NEEDS YOU · 1") and
  `"band"`; new `Row` fields `art: str | None` (poster path), `backdrop: str | None`, `actions:
  tuple[tuple[str, str], ...] = ()` (id, label; e.g. ("choose", "Choose…"), ("rename", "Rename")),
  `note: str = ""` (right-side text such as a time), `code: str = ""` (episode code shown in the accent
  colour on item lines), `faint: str = ""` (old file name shown faint on the right of item lines).
  `VirtualList(..., band_height=64)`; `VirtualList.images: Callable[[Row, int, int], tuple[PhotoImage | None, PhotoImage | None]]`
  (poster, backdrop for a band; may return `(None, None)` until loaded); `VirtualList.on_action:
  Callable[[int, str], None]`; `VirtualList.refresh_images()` (redraw after an image arrived).

A band: backdrop image (or a fill tinted from the title's colour when none), poster (or initials on
a tinted square), title bold 14, `subtitle` muted 11, `note` muted on the right, action buttons drawn
as rounded rectangles (accent-filled for the last, outlined for others) that react to clicks. Item
lines under a band: indented to the band's text, `code` in `palette.accent`, `title` (episode name or
new file name), `faint` right-aligned and cut with an ellipsis. Selection, hover, tick boxes and
remove keep working on item lines.

- [ ] **Step 1: Failing tests:** `test_band_actions_are_clickable` (event at an action's point →
  `on_action(key, "rename")`), `test_band_draws_without_images` (callback returns `(None, None)`: no
  exception, a fill rectangle and initials exist on the canvas), `test_only_visible_bands_ask_for_images`
  (1,000 bands: `images` called only for visible rows), `test_bands_scroll_smoothly` (2,000 rows: a
  redraw stays under the existing list's budget).
- [ ] **Step 2–4:** `PY -m pytest tests/ui/test_virtual_list.py -q` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: show and movie bands in the fast list`.

### Task 5: The Inbox

**Files:**
- Modify: `src/mediaforge/ui/queue_view.py` (title "Inbox"; `build_rows` → `build_inbox_rows`;
  sections; bands; kind filter; band actions; counts to the top band), `src/mediaforge/ui/main_window.py`
  (wire counts), `src/mediaforge/ui/confirm_window.py` (uses `build_inbox_rows` with both kinds)
- Test: `tests/ui/test_queue_view.py`, `tests/ui/test_confirm_window.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: `build_inbox_rows(items, fmt, kind: Literal["series","movies","all"]) -> InboxRows`
  (`rows: list[Row]`, `targets: dict[int, Path]`, `needs: int`, `other_kind: int`). Sections in
  order: "Needs you" (every item not finished: pending, looking up, ready, needs choice, error),
  "Done" (finished in this session, newest first). Within a section, one band per show (episodes;
  key: title + year; subtitle "Season 4 · 2 episodes → Series › Ted Lasso › Season 04" from the
  rendered target's folders) or per movie; bands sorted by title; items by season/episode. Band
  actions: a band with any NEEDS_CHOICE item → ("choose", "Choose…") and ("rename", "Rename");
  other "Needs you" bands → ("rename", "Rename"); "Done" bands → note with the time.
  `QueueView.forge(only: Sequence[int] | None = None)` (None = all pending, as now).
  `QueueView.set_kind(kind)`.

Two narrowings of the spec for stage 1, to be ledgered: "Done" shows what was renamed in this
session (stage 2 adds today's automatic runs), and Done bands show the time but no Undo button
(undo stays in History and in "Undo latest"; stage 2 adds Undo on automatic bands, whose runs it
records per band).

Everything else in the view stays: the header (now "Inbox", count, preset menu, Clear, + Files,
+ Folder), the format entry and sample line, the empty state (text: "Drop video files or folders
here" — "Nothing is renamed until you click Forge."), the detail panel on the right for the selected
line, the bottom bar (chips, Scan subfolders, Preview, Forge, Cancel, progress, Undo latest),
shortcuts, `request_close`, the busy guards. Band "Choose…" opens the match picker for the band's
first unsure item (the queue applies the choice to the band's other items as today); band "Rename"
runs `forge(only=<the band's pending ids>)`.

- [ ] **Step 1: Failing tests:** `test_inbox_groups_episodes_into_show_bands` (two Ted Lasso episodes +
  one Severance → 2 bands with the right subtitles), `test_movies_are_on_the_movies_side`
  (`set_kind("movies")` shows the movie band only; `other_kind` counts), `test_finished_items_move_to_done`,
  `test_band_rename_renames_only_that_band`, `test_band_choose_opens_the_picker_for_the_unsure_item`,
  `test_needs_count_reaches_the_top_band`, `test_inbox_fits_the_minimum_window`.
  Existing queue tests keep passing (their row/key expectations updated to the new row shapes only).
- [ ] **Step 2–4:** `PY -m pytest tests/ui/test_queue_view.py tests/ui/test_confirm_window.py -q`
  FAIL → implement → PASS; the full suite, including the 1,000-item responsiveness test.
- [ ] **Step 5: Commit** `feat: the Inbox with show and movie bands`.

### Task 6: History in bands by day

**Files:**
- Modify: `src/mediaforge/ui/history_view.py`
- Test: `tests/ui/test_history_view.py`

**Interfaces:**
- Produces: day headings ("Today", "Yesterday", "Thu 29 Oct", older with the year) between run
  cards; `RunCard` restyled as a band (fill `palette.surface`, left accent stripe, time large, summary,
  source tag as a chip, Show files / Undo run as the band's buttons). `view.cards`, `card.expand()`,
  `card.checks`, `card.tick()`, `card.selected_ids()`, `summary_text()`, `tag_text()`, `undo_button`
  stay. `view.day_labels() -> list[str]` (for tests).

History ignores the Series | Movies switch in 1.2 (runs mix both kinds and operations don't record a
kind); `set_kind` is a no-op there. This narrows the spec's "the switch filters History": ledger it
and revisit in stage 2, when automatic runs record their kind.

- [ ] **Step 1: Failing tests:** `test_runs_are_grouped_under_day_headings`, `test_day_label_texts`
  (today, yesterday, this week, older), plus the existing History tests unchanged.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: History in bands, grouped by day`.

### Task 7: Settings in tabs, with the accent colour

**Files:**
- Modify: `src/mediaforge/ui/settings_view.py`, `src/mediaforge/app.py` (accent applied at start)
- Test: `tests/ui/test_settings_view.py`

**Interfaces:**
- Produces: `SettingsView.tabs: ctk.CTkTabview` with tabs "Naming" (presets and editor, remembered
  matches), "TMDB", "Right-click menu", "Looks" (theme Light / Dark / Follow Windows, accent swatches
  for the 10 accents, "takes effect the next time MediaForge starts"), "About" (version, updates,
  automatic updates switch, data folder, log folder). `async set_accent(name)` saves `accent`.
  Every existing attribute and method keeps its name.

- [ ] **Step 1: Failing tests:** `test_settings_has_its_tabs` (the five names in order),
  `test_accent_is_saved_and_used_next_start` (`set_accent("teal")` → settings; `palette_for(...,
  "teal").accent` is Teal's), plus the existing Settings tests unchanged.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: Settings in tabs, with AirGrabber's accent colours`.

### Task 8: Everything else in the new colours; release 1.2.0

**Files:**
- Modify: `src/mediaforge/ui/confirm_window.py`, `match_picker.py`, `first_start.py`, `detail_panel.py`,
  `log_strip.py`, `widgets/chips.py`, `widgets/dialogs.py` — only where they hard-code a colour or
  a Studio-specific style (e.g. accent-text-on-amber assumptions, `hover_color=p.warning`);
  `src/mediaforge/__init__.py` (`1.2.0`), `CLAUDE.md` (the look), `README.md` (one line on the look)
- Test: `tests/ui/test_no_hard_coded_colours.py`

- [ ] **Step 1: Failing test:** `test_ui_uses_the_palette` — no `#rrggbb` literal in `src/mediaforge/ui`
  outside `theme.py`, `palettes.py` and `bootstrap_window.py` (plain Tk, documented copies).
- [ ] **Step 2–4:** FAIL → move the literals to palette fields → PASS; the full suite, ruff, pyright.
- [ ] **Step 5:** Real-window check: start the dev copy (temp `MEDIAFORGE_HOME`, own `USERNAME` for
  the lock) in dark and light, Slate and one other accent, with a few sample files; take window
  snapshots (in-process PrintWindow as in 1.0) and look at them; fix what looks wrong.
- [ ] **Step 6: Commit** `release: 1.2.0`. Publishing waits for the owner's OK.
