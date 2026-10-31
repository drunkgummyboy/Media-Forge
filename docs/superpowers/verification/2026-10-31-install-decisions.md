# Installing and updating like AirGrabber: decisions, fixes and deferred items

Copied from the build ledger on 2026-10-31, before the scratch workspace was removed.
Each decision ends with what it costs if it turns out wrong.

## Decisions made during the build (in order)

- Pre-flight: Ruling: every process runs the package check (spec said secondary processes skip it) — the hand-over path needs pydantic, so a missing package would crash right-click launches; the check costs milliseconds and a setup mutex lets only one process run pip — cost if wrong: a few ms per right-click launch.
- Task 2: Ruling: tests/helpers/fake_github.py reuses the generic in-process server from fake_tmdb.py (adds the two GitHub paths and an archive builder) instead of a second server — cost if wrong: none.
- Task 3: Ruling: DisplayIcon is src\mediaforge\assets\MediaForge.ico (the plan said ui\assets; the icon lives in src/mediaforge/assets) — Task 7 uses the same path — cost if wrong: none.
- Task 4: Ruling: ensure_requirements takes run_install/report_failure callables (the launcher passes ui/bootstrap_window's window versions; the default is console output), lookup= and mutex_name= for tests; pip_commands installs only the unmet requirements by name+specifier — cost if wrong: none.
- Task 5: Ruling: the update note is a thin bar above the Activity strip ("MediaForge X is ready…" + "⬆ Restart to update" / "⬆ Update to X") instead of a rail badge — the rail is 52 px wide, too narrow for words — cost if wrong: one bar to move.
- Task 5: Ruling: "Update now"/"Restart to update" go through QueueView.request_close like closing does: a running Forge stops after its current file, then MediaForge restarts; a History undo is not waited for (closing doesn't either today) — cost if wrong: an undo cut off by a restart is finished by crash recovery on next start.
- Task 5: Ruling: LaunchHooks also carries self_test; after shutdown the launcher sends the update step's log lines to mediaforge.log with a temporary handler (the app's logging is shut down by then) — cost if wrong: none.
- Task 5: Ruling: Settings' "Update to X" installs at once (install_archive while running) and restarts, as AirGrabber does; the app does not refresh the Installed-apps entry at start (install.ps1 creates it, the launcher refreshes it after a staged update, and DisplayVersion after an immediate install is refreshed on the next staged update or reinstall) — cost if wrong: Settings › Apps can show the previous version number until then.
- Task 6: Ruling: the menu refresh is context_menu.refresh(all_names, chosen, launcher) (blocking, run in a thread) called from MainShell.refresh_menu at start and on preset changes, instead of app.refresh_menu(services) — MainShell can't import app.py (circular) — cost if wrong: none.
- Task 6: Ruling: menu_presets also falls back to every preset when none of the chosen ones still exist (else the menu would be empty); the icon path moved to context_menu.default_icon (frozen branch removed; settings_view.menu_icon delegates) — cost if wrong: none.
- Task 6: Ruling: tests/conftest.py gained an autouse no_real_menu fixture (installed_command → None, install/uninstall raise) so no test can write the real right-click menu now that preset changes refresh it — cost if wrong: none.
- Task 6: Ruling: fixed a pre-existing flaky test (test_close_during_forge_waits_for_the_run asked to stop before the first move had started; failed 6/8 without this branch's changes) by waiting until a move is in progress — 10/10 after — cost if wrong: none.
- Task 6: Ruling: in an installed copy the automatic first-start import only looks in the current and install folders, so the owner's old mediaforge_config.json (in the Google Drive scripts folder) isn't found automatically; the welcome window's step 1 lets him pick it (existing feature) — spec said "come over automatically" — cost if wrong: one file pick on first start; told to the owner.
- Task 7: Ruling: install failures show a standard Windows message box (TopMost) instead of porting AirGrabber's custom dark Windows Forms dialog (~160 lines) — cost if wrong: a plainer-looking error window.
- Task 7: Ruling: uninstall.ps1 removes the right-click menu itself in PowerShell (only when its command points at this install's main.py) instead of running main.py --remove-menu, so it still works when Python has been removed; it detects a running MediaForge by process command line (AirGrabber's Close-App) rather than the mutex — cost if wrong: none.
- Task 7: Ruling: install.ps1 refuses to reinstall while this copy of MediaForge is running (its files and venv packages would be locked), sets up winget only when Python is missing (AirGrabber needs it for qBittorrent too), and removes a leftover .update folder on reinstall — cost if wrong: one "close it and run again" message.
- Task 7: Ruling: --install-menu keeps the presets picked in Settings (a reinstall no longer resets them; new installs get all) — test_install_menu_keeps_the_presets_picked_before — cost if wrong: none.
- Task 7: Ruling: the opt-in installer test is switched on with MEDIAFORGE_INSTALLER_TESTS=1 (a skipif) instead of a pytest marker; it passed once in this task (77 s, real pip, folder with spaces) — cost if wrong: none.
- Task 8: Ruling: no pytest "installer" marker was added (Task 7's opt-in test uses an environment variable); the icon/logo parity item now points at test_no_secrets.py::test_assets_ship_inside_the_package — cost if wrong: none.
- Task 8: Ruling: besides the NAS name, the personal folder path in the new design and plan was replaced with "the AirGrabber project folder" before publishing; the Hotmail address stays in them (already public) — cost if wrong: none.
- Ruling: the final whole-branch review and its fix pass run before Task 9 (publishing), so only reviewed code goes online — cost if wrong: none.
- Final: Ruling: reviewer disagreed with "every process runs the package check" (Pre-flight) — accepted: a launch that finds another MediaForge holding the lock now hands over without the check and never runs pip next to it (test_hand_over_skips_the_package_check_while_a_window_is_open) — cost if wrong: none.
- Final: Ruling: reviewer disagreed with Task 5 "History undo not waited for" — accepted and fixed (above) — cost if wrong: none.
- Final: Ruling: reviewer disagreed with Task 5 "no Installed-apps refresh at start" — accepted: the launcher refreshes the entry after every run of an installed copy — cost if wrong: none.
- Final: Ruling: plan issue (minor 14) — older commits still contain the NAS name and a personal folder path, and Task 9's full-history push would publish them; put to the owner at Task 9 together with the publish OK — cost if wrong: the NAS name and Windows user name become readable in the page's history.
- Final: Ruling: python.org 3.13.15 and winget v1.29.380 URLs not checked offline (declined) — same pins as AirGrabber, which installs with them — cost if wrong: the python.org fallback fails on PCs without winget; the owner's VM check catches it.
- Final: Ruling: the newer Python Install Manager's aliases vs the probe (declined) — Test-Python only accepts an interpreter that runs the probe and reports a real path — cost if wrong: the installer installs Python again.
- Final: Ruling: ARM64 PCs get the amd64 Python from python.org (declined) — as AirGrabber; runs under emulation — cost if wrong: slower on ARM.
- Final: Ruling: an installer run from an elevated PowerShell starts MediaForge elevated in that first session (declined) — drag and drop from Explorer is blocked into an elevated window until MediaForge is restarted normally — cost if wrong: one confusing first session; README says to open plain Windows PowerShell.
- Final: Ruling: the look of the update bar and About section (declined) — not driven in a real window here; the owner sees it in checklist step 2 — cost if wrong: layout polish.
- Final: Ruling: the real install round trip (declined by the reviewer under its limits) — re-run after the fix pass — cost if wrong: none.
- Final: Ruling: legacy HKLM menu removal (declined) — spec non-goal; Settings offers the removal .reg — cost if wrong: none.
- Final: Ruling: "Update now" swaps files in while the old process keeps running and may lazily import new files before restarting (declined) — same as AirGrabber; the shutdown path's lazy imports were checked low-risk — cost if wrong: a rare error on that one shutdown, shown by the new crash report.
- Final: Ruling: Task 9 publishing not reviewed (declined) — it needs the owner's OK first — cost if wrong: none.
- Task 9: Ruling: published as one fresh root commit (owner's choice) instead of the plan's author rewrite + full-history force push; the full history stays on the local branch history/full (never pushed: older commits hold the NAS name and a personal path) — cost if wrong: the public page has no step-by-step history.

## Fixed after the final review

- fixed C1 non-ASCII user folder broke Test-Python — test_find_python_keeps_non_ascii_paths_intact RED→GREEN (path now travels as UTF-8 hex), suite 478/478 (+1 opt-in skipped)
- fixed I2 interrupted swap could leave a copy that never starts — test_power_cut_at_any_step_never_breaks_the_install[1-6], test_finished_swap_with_leftover_staging_keeps_the_new_version, test_recovery_reloads_the_restored_code RED→GREEN (main.py replaced in place and never missing; main.py restores the old version at start), suite 478/478
- fixed I3 hand-overs dropped during shutdown — test_launch_during_shutdown_waits_for_the_lock_and_opens_the_file, test_launch_gives_up_after_the_deadline RED→GREEN, suite 478/478
- fixed (found while fixing I3) a launch arriving during an update swap could run the power-cut repair mid-swap — test_power_cut_repair_waits_while_another_mediaforge_holds_the_lock RED→GREEN, suite 478/478
- fixed I4 a development copy rewrote the real right-click menu — test_refresh_never_takes_over_another_copys_menu[two cases] RED→GREEN, suite 478/478
- fixed I5 start-up crashes were invisible — test_a_crash_is_reported_and_the_update_and_lock_still_happen, test_a_crash_in_the_package_check_is_reported, test_default_crash_report_logs_and_shows_a_message RED→GREEN, suite 478/478
- fixed I6 a failed pip install in a burst ran and reported once per process — test_failed_install_in_a_burst_runs_pip_and_reports_once, test_a_launch_that_never_gets_the_lock_does_not_run_pip RED→GREEN, suite 478/478
- fixed (minor 7, re-graded) restart/close didn't wait for a History undo — test_restart_waits_for_an_undo_in_history RED→GREEN, suite 478/478
- fixed (minor 8) Installed-apps version stale after "Update now" — test_installed_apps_entry_is_refreshed_even_without_a_staged_update RED→GREEN, suite 478/478

## Small issues left for later

- clicking "Update now" during a Forge stops the run after the current file and the rest of the queue is gone after the restart, with no warning.
- UpdateService.install / install_archive don't require the download to be newer than the running version (a GitHub cache lag could reinstall the same version).
- the "Update to vX" bar opens Settings showing "Check for updates" instead of the version already found.
- LAUNCHER uses Path.resolve() but the scripts use the unresolved install path; on a profile reached through a junction, uninstall can miss the menu and the entry refresh says "another folder".
- a real Microsoft Store Python 3.13 is accepted; Store Python may redirect AppData/registry writes (unverified; check on the VM).
- a Python 3.13 without tkinter makes the installer try to install Python again, which then can't be "found" (loop of "run again").
- the README's development section suggests `python main.py`; run with a system Python it pip-installs the dependencies into that Python — say to use a venv first.
