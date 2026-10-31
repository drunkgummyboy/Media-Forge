# Legacy parity verification: MediaForge 1.0.0

Every capability of the legacy `MediaForge.py` (spec Appendix A) maps to automated tests,
listed in `tests/test_legacy_parity.py`. That test fails if a referenced test disappears.
This page records the checks that need a person or a real window, and what was seen.

## Automated

`PY -m pytest` covers all 23 items: parser, formatter, planner, executor, undo, cleanup,
TMDB client (against an in-process fake server), trailers (with a fake yt-dlp), database,
legacy import, single-instance hand-over, context-menu entries, and the UI views (shared
Tk root).

## Checked in a real window (snapshots captured from inside the app with PrintWindow)

| Check | How | Result |
|---|---|---|
| Queue view matches the Studio mockup | 6 sample files, Series (Folder) preset, real TMDB lookups | Pass: grouped rows, SxxEyy in amber, `?` on uncertain matches, poster and episode name in the detail panel. Layout fixes made on the way: bottom bar split into two rows, chip width, group labels. |
| History view | Forge 6 files, open History, expand the run | Pass: run cards, Show files with tick boxes, Undo run / Undo N selected. A poster-clearing crash was found and fixed. |
| Settings view | Open Settings | Pass: Appearance, TMDB, Presets editor with live sample, menu and matches sections. Preset highlight fixed. |
| Confirm window | Router opens a confirm window for 6 files | Pass: matches the mockup, inline "TMDB isn't sure · Change…". |
| Right-click hand-over | 3 processes started at once with `--confirm` (as Explorer does) | Pass after fixes: one window with 3 files; the extra processes exit 0. The key-file race and late listener were found here and fixed. |
| Packaged program (1.0.0; since 1.1.0 MediaForge installs from source with `install.ps1`, see the 2026-10-31 install checklist) | `release/build.py`: PyInstaller one-folder build, zip (28 MB), unzip to temp, `MediaForge.exe --self-test`; then launched the unzipped .exe with a fresh data folder and two sample files | Pass: self-test OK; the welcome window showed all three steps and the two files were queued (log). The first build died at startup (babelfish loads modules by name); fixed with a regression test. |

## Not checked here (needs the owner)

- Dragging files in from Explorer onto the window (the drop handler is tested; the OS
  drag itself isn't automated).
- The real Explorer right-click menu: adding it writes to the user's registry, so it
  wasn't installed during development. The registry entries are unit-tested and the
  hand-over was simulated with three real processes.
- Renaming on the NAS share (`\\NAS\...`): tests on local folders check that every
  disk call gets the `\\?\` long-path form (`tests/core/test_long_paths.py`), but this
  PC has long paths switched on in Windows, so a deep folder on a PC without that
  setting, and the share itself, were never tried for real.
- Trailer downloads from YouTube: the tool download, checksum check and command line are
  tested against fakes; no real YouTube download was made.
