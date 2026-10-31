# MediaForge rewrite: decisions, fixes and deferred items

Copied from the build ledger on 2026-10-26, before the scratch workspace was removed.
Each decision ends with what it costs if it turns out wrong.

## Decisions made during the build (in order)

- T4→T5: Database consumed by HistoryRepo; T4 must create runs/operations tables. Ruling: history tables are created in Database schema v1 (added in T5 by editing database.py, version stays 1 because nothing is released) — matches T5 note — cost if wrong: none.
- Task 2: Ruling: guessit labels 'Blade Runner 1982 The Final Cut' an episode with no numbers, and date-based shows have no S/E — both become MOVIE (episode_title dropped for movies; date year used when no year) — same as legacy behaviour — cost if wrong: such files need a TV preset.
- Task 2: Ruling: pytest --import-mode=importlib added so tests/core and tests/db may share basenames — cost if wrong: none.
- Task 3: Ruling: values are cleaned with a light _clean_text (no reserved-name suffix) and the format is split into segments before substitution, instead of sanitize_segment on values — 'Con Air' must not become 'Con_ Air'; same guarantee that values can't create folders — cost if wrong: none (tested).
- Task 3: Ruling: Rendered.relative is PureWindowsPath (a PurePath) so paths render with backslashes on any OS — cost if wrong: none.
- Task 6: Ruling: Plan.clean_roots / prune_dirs are left empty when the clean_junk / prune_empty options are off, so Preview's description matches what will run — cost if wrong: none.
- Task 6: Ruling: trailers are planned for any kind when the trailer option is on (as legacy), de-duplicated per destination folder — cost if wrong: one trailer per season folder for TV presets that enable trailers.
- Task 7: Ruling: PlannedPoster/PlannedTrailer gained item_ids (planner fills them) and the executor creates artwork only when one of those files was renamed or already named — spec §4 anti-goal (no orphan artwork for failed files) — cost if wrong: none.
- Task 7: Ruling: posters and trailers run after all renames instead of interleaved per file — same end state, simpler journal — cost if wrong: artwork appears a bit later during long runs.
- Task 7: Ruling: HistoryRepo.mark_done gained dst= so a trailer op records the file actually created (.mp4 or .url fallback) — cost if wrong: none.
- Task 7: Ruling: fsops also exposes missing_dirs/make_dir/exists/is_dir/is_empty_dir; executor journals each created folder before mkdir, so a pre-existing folder (race) is recorded failed and never removed by undo — cost if wrong: none.
- Task 8: Ruling: an undo of a created file that is already gone is marked DONE (goal reached, so the run can become fully undone) and still reported as 'already gone'; deleted junk is reported once (marked skipped) and then left out of later undos — cost if wrong: one extra line in the undo summary.
- Task 9: Ruling: aioresponses 0.7.9 is incompatible with aiohttp 3.14 (ClientResponse needs stream_writer); tests use an in-process aiohttp TestServer fake (tests/helpers/fake_tmdb.py) and TmdbClient takes base_url/image_url — plan allowed either approach; aioresponses dropped from dev deps; pytest pythonpath gets tests/ — cost if wrong: none.
- Task 9: Ruling: 'retries 3' read as 3 attempts total (delays 0.5 s, 1 s), Retry-After honoured — cost if wrong: one fewer retry on a flaky network.
- Task 10: Ruling: Resolver depends on a TmdbLike Protocol (TmdbClient satisfies it) so tests use a plain fake; a manual pick is stored with state REMEMBERED — cost if wrong: none.
- Task 11: Ruling: filename parsing for newly added files also runs in a worker thread (guessit is CPU-heavy for 1,000 files), not just the folder scan; Queue depends on a ResolverLike Protocol — cost if wrong: none.
- Task 12: Ruling: legacy '--extractor-args youtube:client=tv,mweb,ios' is not valid yt-dlp syntax (player_client) and is dropped — current yt-dlp picks clients itself and self-updates; progress lines aren't parsed (rows show working/done) — cost if wrong: a few trailers fail where a forced client would have worked; falls back to a .url link.
- Task 12: Ruling: after a failed download, yt-dlp -U runs and the download is retried once before writing the .url fallback; without ffmpeg the single-file format best[ext=mp4]/best is used — per spec §8.10.
- Task 13: Ruling: an existing TMDB key in the new settings is never replaced by the legacy one; the import still marks itself done when no legacy files exist, so it never runs twice — cost if wrong: none.
- Task 13: Ruling: added shutdown_logging() (closes the file handler; needed for clean re-configuration and Windows temp cleanup) and redaction of exception tracebacks — cost if wrong: none.
- Task 14: Ruling: legacy_removal_reg also deletes the old CommandStore\shell\MediaForge_* keys, read from the old menu's SubCommands value (HKLM read needs no admin) — otherwise the old entries' commands would linger — cost if wrong: none.
- Task 14: Ruling: ConfirmBatcher.add/flush_due take an optional 'now' so tests drive time; the UI calls flush_due from Tk 'after' — cost if wrong: none.
- Task 15: Ruling: creating fresh Tk roots repeatedly in one process fails at random on this Windows/Tcl setup (msgcat 'couldn't read nl.msg'); MainWindow is split into MainShell (a frame, built inside a shared session root in tests) and MainWindow (the single real root); save_window_state(root, services) replaces close-on-MainWindow in tests; self_test(root=) reuses the test root — cost if wrong: none for users.
- Task 15: Ruling: added ui/gc_guard — automatic GC is disabled and the Tk thread collects every 3 s, because GC in asyncio.to_thread workers freed Tk images off-thread and each stalled ~1 s (measured: 11 s in one test, also a real-app stall risk when posters change) — cost if wrong: reference cycles live up to 3 s longer.
- Task 15: Ruling: Services gained queue_events (EventHub), tmdb_ref (TmdbRef) and close_services(); Resolver.set_client swaps the TMDB client after a key/language change; the executor fetches posters through a live adapter — cost if wrong: none.
- Task 15: Ruling: the bundled logo is a 512 px copy of MediaForge.png (the original is 2 MB) — cost if wrong: none.
- Task 16: Ruling: VirtualList code was drafted before its tests; tests were then written and run RED with the module moved aside, GREEN with it restored — cost if wrong: none (behaviour is pinned by the tests).
- Task 16: Ruling: filename parsing moved from a worker thread onto the event loop in chunks of 8 (core/queue.py _parse_all) — guessit CPU work in a thread starved the Tk thread of the GIL (measured 1–2.5 s window stalls with 1,000 files; now 7 ms); gc_guard now collects gen0 every 100 ms, gen1 every 2 s, gen2 every 30 s and freezes startup objects (one 3 s full collect cost 120 ms) — cost if wrong: parsing 1,000 names takes ~5 s of loop time in 40 ms slices.
- Task 16: Ruling: Primary.close() wakes the listener with a bare pipe open/close instead of a Client() handshake, which hung forever when the listener had already stopped (seen as an intermittent full-suite hang; regression test added, RED on old code) — cost if wrong: none.
- Task 16: Ruling: DetailPanel.show(item, target) takes the destination path too; MatchPicker.ask(master, item, services, palette) takes the palette; QueueView exposes settled(), set_format(), move_selection(), shortcut() and an injectable ask() for dialogs — cost if wrong: none.
- Task 16: Ruling: group headings show the deepest destination folder (grouping still by full path); the preview line is relative to the dropped folder; the bottom bar is two rows (option chips, then Preview/Forge) so buttons never get pushed off-screen — found in a real-window snapshot — cost if wrong: none.
- Task 17: Ruling: created/removed folders are hidden in the expanded run list; undoing selected files also tries that run's created folders (removed only if empty) — cost if wrong: an emptied folder could linger after a partial undo.
- Task 17: Ruling: fixed a real DetailPanel bug found in a snapshot — CTkLabel ignores image=None and kept a freed poster image (TclError 'image pyimage… doesn't exist'); a transparent 1 px image now replaces it; regression test RED→GREEN — cost if wrong: none.
- Task 17: Ruling: HistoryView takes injectable confirm()/inform() for tests; skipped undo items are listed in an OK dialog (max 15 lines) — cost if wrong: none.
- Task 18: Ruling: import_legacy gained force=True so the first-start picker can import a chosen file after the automatic first-run import found nothing — cost if wrong: none.
- Task 18: Ruling: fixed a Tk crash found by the suite — pop-ups marked transient() with a geometry, destroyed before being shown, made the next widget build segfault (access violation in update_idletasks); all pop-ups now use widgets/popups.prepare_popup (topmost + lift + focus, as the legacy app did); regression test crashes on old code, passes now — cost if wrong: dialogs stay above other apps while open.
- Task 18: Ruling: FirstStart is shown from app.run as a task over the main window (not for --confirm launches); Settings saves the TMDB key/language and rebuilds the client in place; theme applies on restart — cost if wrong: none.
- Task 19: Ruling: added ui/router.WindowRouter (confirm batching, plain launches raise the main window, confirm-only launches exit when the last confirm window closes) instead of putting the routing inside app.py — testable without a real process — cost if wrong: none.
- Task 19: Ruling: fixed two real hand-over bugs found by simulating Explorer (3 processes at once): the pipe key file raced (each process wrote its own key; now created once via atomic rename, test RED with 8 different keys → GREEN), and the primary started listening only after loading; now it listens first and an Inbox holds early messages, extra launches import only the light modules and retry up to 15 s — result: 3 launches → 1 window with 3 files, extras exit 0 — cost if wrong: none.
- Task 19: Ruling: in a confirm window, uncertain matches use TMDB's top result unless the user clicks Change…; trailers are skipped there if the tools aren't installed yet (logged) — cost if wrong: a right-click rename can use a wrong-but-highlighted match if the user ignores the note.
- Task 20: Ruling: build files live in release/ (not packaging/) — a top-level 'packaging' folder would shadow the PyPI 'packaging' library on the test path — cost if wrong: none.
- Task 20: Ruling: the packaged self-test caught a real startup crash (babelfish/guessit load modules by name: 'No module named babelfish.converters.alpha2', shown in a modal error box so the self-test hung); the spec now collects their submodules and metadata; regression test RED→GREEN; build.py reports a hang as an error-box hint — cost if wrong: none.
- Task 21: Ruling: the four gap tests (detail panel fields, shortcut actions, log colours, bundled assets) characterise behaviour built in earlier tasks, so they passed on first run; the parity table test itself fails if any referenced test is renamed or removed — cost if wrong: none.
- Final: Ruling: confirm window lists files with group headings and a "Change match…" button for the chosen row (any row, not only uncertain ones); uncertain rows say "TMDB isn't sure, using …" under the name — the per-row widgets were what froze it — cost if wrong: one extra click to change a match.
- Final: Ruling: Task 7 artwork-after-renames stands (reviewer partly disagreed) — renames are the part that must stay safe and they all finish first; Cancel is not checked during the poster/trailer phase — cost if wrong: Cancel during a run with trailers can take minutes to take effect.
- Final: Ruling: the confirm window stays always-on-top (reviewer: owner should decide) — Task 18's crash fix needs topmost pop-ups; raised with the owner — cost if wrong: the confirm window covers Explorer while it is open.
- Final: Ruling: yt-dlp progress lines are not parsed (reviewer: deviation from spec §8.10, show the owner) — the run's progress bar shows the trailer step, not a per-trailer percentage; raised with the owner — cost if wrong: no download percentage while a trailer downloads.
- Final: Ruling: how closely the screens match the Studio mockups, and the light theme (declined to judge) — the dark theme was checked in real-window snapshots; the light theme was not — cost if wrong: visual mismatches the owner will see on first look.
- Final: Ruling: guessit accuracy beyond the 60+ name table (declined to judge) — same library as the legacy app, and every new name is shown before anything changes — cost if wrong: an odd release name needs a manual match.
- Final: Ruling: real YouTube downloads and dropping the legacy --extractor-args flag (declined to judge) — owner-only check; a failed download leaves a YouTube search shortcut — cost if wrong: .url shortcuts instead of trailers until yt-dlp flags are adjusted.
- Final: Ruling: Explorer starts one process per selected file (declined to judge) — spec §8.9 design; extra processes hand over and exit, the batcher merges them into one window (3-process simulation passed) — cost if wrong: a selection of hundreds of files is slower to appear.
- Final: Ruling: the same file via a mapped drive and via its UNC path counts twice (declined to judge) — spec dedupe is per path; the second rename fails for that file alone, nothing is overwritten — cost if wrong: one confusing "failed" line.
- Final: Ruling: names over 255 characters per segment (declined to judge) — fail with a clear per-file error — cost if wrong: that file must be renamed by hand.
- Final: Ruling: Synology shares set to case-sensitive (declined to judge) — unusual setting, not supported specially — cost if wrong: a case-only rename on such a share may be judged wrong by recovery.
- Final: Ruling: pickle over the authenticated, same-user named pipe (declined to judge) — acceptable trust boundary — cost if wrong: none outside the user's own account.
- Final: Ruling: TMDB key stored as plain text in the %APPDATA% database (declined to judge) — same as the legacy app; the spec doesn't ask for encryption — cost if wrong: anyone using the same Windows account can read the key.
- Final: Ruling: PyInstaller manifest longPathAware unchecked (declined to judge) — every disk call now adds the \\?\ prefix itself, so the setting isn't relied on — cost if wrong: none known.
- Final: Ruling: main queue list speed trusted to its 1,000-item test (declined to judge) — stands — cost if wrong: none known.
- Final: Ruling: licence completeness in the zip (declined to judge) — release/collect_licenses.py gathers every installed package's licence files; guessit and rebulk ship as .py — cost if wrong: a missing notice to add before sharing widely.
- Final: Ruling: legacy HKLM removal .reg not checked against the owner's registry (declined to judge) — the owner runs it; nothing was written to the registry during development — cost if wrong: old menu entries may remain and need removing by hand.

## Fixed after the final review

- fixed C1 confirm window / History freeze on big selections — test_three_hundred_files_redraw_without_freezing (8.1 s → <0.25 s), test_show_files_for_a_big_run_opens_quickly (3.1 s → <0.3 s), test_reload_gives_the_window_turns_while_building_cards (0.74 s → <0.2 s), test_tick_boxes_toggle_on_click_and_respect_disabled, test_clicking_a_tick_box_unticks_that_file RED→GREEN, suite 393/393 (commit c4eddec)
- fixed I1 double Forge/Rename/Undo — test_forge_twice_at_once_runs_once, test_rename_twice_at_once_runs_once, test_undo_twice_at_once_asks_once, test_two_undos_at_once_undo_once, test_install_twice_at_once_downloads_once RED→GREEN, suite 393/393
- fixed I2 finished files coming back as to-do — test_finished_items_stay_finished, test_renamed_files_keep_their_new_path RED→GREEN, suite 393/393
- fixed I3 overlapping drops / clear during add — test_two_drops_at_once_add_each_file_once, test_clear_during_add_discards_it RED→GREEN, suite 393/393
- fixed I4 close mid-run drops journal entries — test_run_with_pending_ops_is_kept, test_cancelled_task_still_journals_the_rename, test_close_during_forge_waits_for_the_run, test_cancel_kills_ytdlp RED→GREEN, suite 393/393
- fixed I5 recovery with the share offline / case-only renames — test_unreachable_share_leaves_ops_pending, test_case_only_rename_judged_by_real_name RED→GREEN, suite 393/393
- fixed I6 cross-volume fallback deleting a file — test_cross_volume_never_deletes_a_file_that_appeared_meanwhile, test_cross_volume_locked_source_leaves_no_copy RED→GREEN, suite 393/393
- fixed I7 long paths outside fsops — test_scan_walks_with_extended_paths_and_returns_normal_ones, test_planner_checks_existence_with_extended_paths, test_cleanup_walks_with_extended_paths RED→GREEN, suite 393/393
- fixed I8 blocking folder check on the window thread — test_folder_checks_never_run_on_the_loop_thread RED→GREEN, suite 393/393
- fixed I9 series poster root for titles with punctuation — test_series_poster_root_for_titles_with_punctuation, test_series_poster_falls_back_to_first_folder_like_legacy RED→GREEN, suite 393/393
- fixed parity record overstating long-path coverage (reviewer disagreement with Task 13) — wording corrected; now backed by tests/core/test_long_paths.py, suite 393/393

## Small issues left for later

- trailer .url fallback overwrites an existing .url and journals it as created, so undo could delete a .url that was there before.
- % in titles isn't escaped in the yt-dlp -o template, and a .webm result isn't recognised (orphan file plus a .url).
- Cancel isn't checked during the poster/trailer phase and the progress bar sits idle there.
- folders created just before a failed rename stay behind (journaled, but pruning ignores them).
- the confirm window shows names before collision handling; the real rename may add " (1)".
- Tk callback and thread exceptions aren't logged (stderr is gone in the windowed exe).
- cache purge is never called and old poster images are never deleted (spec §8.5: 7 and 30 days).
- renaming a preset to an existing name deletes the other preset; "New" with an existing name overwrites it; renamed presets leave the Explorer menu pointing at a stale name.
- if hand-over to the running MediaForge fails, the right-click does nothing visible.
- only TmdbError is caught during lookups; any other error leaves an item "looking up" forever.
- the detail panel poster flickers ("Loading…") on every refresh during lookups and runs.
- a forced re-import on first start can add the legacy undo run to History a second time.
- the journal uses synchronous=NORMAL; a power cut could lose a pending row written just before a rename.
- no dependency lock file; pyright needs --pythonpath (no venvPath in pyproject).
- in right-click-only mode the "trailers skipped" note goes to the hidden main window's log.
- "Undo N selected" also tries the run's folders, so "folder isn't empty" can be listed for folders the user didn't tick.
