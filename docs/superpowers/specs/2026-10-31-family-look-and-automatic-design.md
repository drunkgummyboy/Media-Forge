# MediaForge 1.2: AirGrabber's family look, and renaming on its own

Date: 2026-10-31. Status: draft for the owner's approval.

## Intent

The owner wants MediaForge to work **mostly automatically**: a download finishes, and the episode
or movie ends up correctly named in the right folder on his NAS without him doing anything. That
does not mean the window may be plain: it must be nice and easy, and look like it belongs to the
same family as his other program, AirGrabber (the AirGrabber project folder; its window is
`app/ui/main_window.py`, its colours `app/ui/theme.py` and `app/ui/palettes.py`).

Today MediaForge 1.1.x only renames files you drop on it or right-click, inside the folder they
are in, with its own dark "Studio" look (amber accent, icon rail, list and detail panel).

## What the owner decided

1. "Most of it automatic", but the window stays nice and easy.
2. One family look with AirGrabber.
3. Keep the text lines "sort of", but spruce them up.
4. Create the **Inbox**.
5. MediaForge watches AirGrabber's downloads, and **must know a show from a movie: they are
   handled differently**.
6. Accepted ("looks alright"):
   - MediaForge starts with Windows and waits in the tray, watching the download folder.
   - A new video is handled once the download has really finished: looked up on TMDB, named with
     a preset, moved into the library.
   - A **sure** match is renamed and moved on its own, with its poster; it shows up in History and
     can be undone as now.
   - An **unsure** match, or a NAS that can't be reached, waits in the Inbox; the tray icon shows a
     count; one click confirms or picks the right show.
   - A trial period that only shows what MediaForge *would* do, until the owner switches it to
     doing it.
   - A daily summary instead of a notification per file.
   - Cleaning up the download folder after moving (samples, .nfo files, empty folders).
7. Picked mockup **B, "show bands"** (`.superpowers/brainstorm/…/inbox-look.html`, kept out of
   git): a top band with MediaForge's name, a **Series | Movies** switch like AirGrabber's, the
   pages (Inbox with a count, History, Settings) and a "Watching downloads" light; in the Inbox a
   "Needs you" part above "Done automatically today"; every show a wide band with its backdrop and
   poster, its episodes as short lines under it (episode code, episode name, the old file name
   faint on the right).
8. Accepted as played back (with the marked guesses):
   - Movies: each movie its own band, one line with the new and old file name.
   - "Needs you" bands have **Choose…** and **Rename**; "Done automatically" shows the time and an
     **Undo** when pointed at.
   - Files dropped on the window or right-clicked land in the Inbox too: one list, no separate
     Queue.
   - History: the same bands, grouped by day, still undo for the last 50 runs.
   - Settings: a window in AirGrabber's style with tabs.
   - AirGrabber's colours and top band, light and dark following Windows, AirGrabber's accent
     colours.
9. **Every folder is a setting**: the watched download folder(s), the Series library folder and
   the Movies library folder are chosen in Settings (not assumed). For reference: AirGrabber on
   this PC saves its .torrent files to `\\NAS\Multimedia\` for the NAS's Download Station,
   so finished downloads are on the NAS too.

10. **Finished downloads are moved, not copied**, into the library. The owner accepts that
    Download Station then stops sharing (seeding) that download and may show it as an error.

## Goals

- A finished download is renamed and moved into the right library folder without a click when
  MediaForge is sure, and waits in the Inbox when it isn't.
- Shows and movies each have their own library folder and their own naming preset.
- The window looks and behaves like AirGrabber's sibling, in light and dark, with its accents.
- Everything stays undoable through History, as today.

## Non-goals (for 1.2)

- A Library page (shelves of what is on the NAS, missing episodes).
- A detail window per show or movie like AirGrabber's.
- Downloading anything; talking to AirGrabber, Download Station or qBittorrent directly
  (MediaForge only watches folders).
- Dutch texts (MediaForge stays English).
- Watching on the NAS itself (the PC must be on).

## Anti-goals (a failure even if it works)

- Touching a file that is still downloading, or a file outside the watched folders and the
  library folders.
- Overwriting anything, or a move that leaves a file half on the NAS, or a rename/move without a
  History entry (the journal-before-act rules of 1.x stay).
- Guessing silently: a show taken for a movie (or the other way round) and moved, or an unsure
  match moved without the owner's click.
- Notification spam, or a tray icon that nags.
- A watcher that makes the PC or the NAS busy (constant full scans, waking disks all night).
- A window that is slower than 1.1 with many files (thousands of lines must still scroll smoothly).
- Losing anything the 1.x window could do: presets and their editor, right-click confirm window,
  drag and drop, posters, trailers, the format sample, remembered matches, undo per file or run.

## Constraints

- Everything in `CLAUDE.md` holds: one asyncio loop, `gc_guard`, parsing on the loop, long paths,
  `prepare_popup`, light imports for secondary processes, the window shown with `update()`.
- The NAS is reached over SMB (`\\NAS\...`). Windows' change notifications over SMB can be
  missed: the watcher must work without them (a periodic look is the base; notifications, if
  used, only make it quicker).
- Install and update stay as in 1.1 (`install.ps1`, self-update from GitHub). New dependencies
  (pystray for the tray, as AirGrabber) go in `pyproject.toml`; the launcher installs them.
- Existing data carries over: presets, history, remembered matches, settings.

## How it works

### Two stages (Claude's call)

1. **1.2.0, the family look:** new theme and top band, the Inbox replacing the Queue for dropped
   and right-clicked files (show bands), History and Settings restyled, the right-click confirm
   window restyled. Nothing automatic yet; shipped and used for a few days.
2. **1.3.0, automatic:** watched folders, the Series and Movies library folders, automatic
   renaming of sure matches with the trial setting, tray icon, start with Windows, notifications
   and the daily summary, download-folder clean-up.

### The look (stage 1)

- **Colours:** AirGrabber's tokens (light, dark) pairs and its accent palettes, ported into
  `ui/theme.py` (Slate default; the accent is a setting, a change takes a restart as in
  AirGrabber). Light/dark/follow-Windows replaces 1.x's Dark/Light/System.
- **Top band** (replaces the icon rail): MediaForge's logo and name, the **Series | Movies**
  switch, the pages Inbox (with a count of what needs the owner) · History · Settings, and on the
  right the watching light (stage 2; in stage 1 it reads "Drop files here").
- **Inbox page** (replaces the Queue view):
  - Sections "Needs you" (unsure matches, errors, and in stage 1 everything not yet renamed) and
    "Done today" (what was renamed today; a Done line has Undo when pointed at).
  - The Series side shows episodes; the Movies side shows movies. A dropped file of the other kind
    is counted on the other side's switch label ("Movies · 2").
  - A **show band**: backdrop (TMDB, faded into the window colour), poster, show title (with "?"
    when unsure), a muted line ("Season 4 · 2 episodes → Series › Ted Lasso › Season 04") and the
    band's buttons (Choose…, Rename, or the time when done). Episode lines under it: episode code
    in the accent colour, episode name, old file name faint on the right.
  - A **movie band**: the same band with one line: new file name, old file name.
  - Thousands of lines stay smooth: bands and lines are drawn on a canvas like 1.1's fast list
    (`ui/widgets/virtual_list.py`), backdrops and posters from the existing image cache.
  - The 1.1 actions stay reachable: the preset choice and Preview/Rename for the selection, the
    option chips (posters, trailers, clean-up), the format sample, Choose… (match picker), the
    remembered matches, keyboard shortcuts.
- **History:** the same bands grouped by day ("Today", "Yesterday", "Thu 29 Oct"), each run
  undoable whole or per line, as now.
- **Settings:** a window in AirGrabber's settings style with tabs: *Library* (stage 2: watched
  folders, Series folder, Movies folder, automatic on/trial/off, start with Windows, clean-up),
  *Naming* (presets and the preset editor; which preset Series and Movies use), *TMDB*,
  *Right-click menu*, *Looks* (light/dark, accent), *About* (version, updates, log folder).
- **Right-click confirm window and dialogs:** restyled with the new colours; same behaviour.

### Renaming on its own (stage 2)

- **Watching:** each watched folder is looked at every few minutes (and sooner when Windows
  reports a change, if that works for the share). Only video files count; subfolders are included.
- **Finished:** a file is handled only when its size and time haven't changed for two looks in a
  row, it can be opened exclusively, and it isn't a temporary download file (`.part`, `.!qB`, and
  Download Station's own temporary names).
- **Show or movie:** the file name (guessit) and the TMDB match must agree. When they don't, or
  TMDB is unsure, the file goes to the Inbox as unsure; it is never guessed.
- **Where it goes:** shows → the Series library folder with the Series preset (e.g. Series (Folder));
  movies → the Movies library folder with the Movies preset. The presets keep doing the naming
  inside that folder, exactly as today; an existing show folder is reused.
- **Sure** (a confident or remembered TMDB match): renamed and moved, with poster/trailer as the
  preset says, journaled as one run per batch with the source "automatic", shown under "Done
  today".
- **Trial** (the setting's first state when the owner turns automatic on): sure matches are shown
  under "Would rename" with the destination, and nothing moves until the owner clicks Rename or
  switches to automatic.
- **Not reachable / failed:** the file stays where it is, shows under "Needs you" with the reason,
  and is tried again on a later look.
- **Clean-up:** after a successful move, junk next to it (sample files, .nfo, .txt, .url, empty
  folders) inside the watched folder is removed, using 1.x's clean-up rules (journaled; junk can't
  be restored, as today).
- **Tray:** a tray icon (pystray, as AirGrabber's `app/ui/tray.py`) with the count of "Needs
  you"; closing the window keeps MediaForge in the tray; the menu opens it or quits. **Start with
  Windows** (a Startup-folder shortcut with `--tray`, as AirGrabber's `app/services/startup.py`,
  carrying MediaForge's AppUserModelID).
- **Notifications:** something needs the owner → one notification per batch ("2 files need you").
  Done automatically → one **daily summary** ("Today: 5 episodes and 1 movie renamed"), at a quiet
  moment in the evening; both can be turned off.

## Testing

- Pure parts (finished-file rule, show/movie decision, destination, batching, trial, summary
  text) as unit tests; the watcher against temporary folders with fake clocks.
- UI: the Inbox and History bands with 1,000+ lines stay as smooth as 1.1's list (the existing
  responsiveness tests); the real-start test (window shows) stays.
- Owner checks: a real download from AirGrabber through Download Station into the library, a
  movie and an episode, an unsure match, the NAS switched off mid-way, a week in trial.

## Calls Claude made (for the owner to check)

- Two releases: the look first (1.2), the automatic part after (1.3).
- The Inbox replaces the Queue; the icon rail gives way to AirGrabber's top band.
- The Series | Movies switch filters the Inbox and History; a file of the other kind shows as a
  count on the switch.
- Show/movie must be agreed by the file name and TMDB, otherwise the file waits in the Inbox.
- Automatic renaming starts in **trial**; full automatic is one switch in Settings.
- One run in History per batch of automatic renames, marked "automatic".
- Notifications: per batch for "needs you", one evening summary for "done".
- AirGrabber's accent colours, with Slate (its default) as MediaForge's default too.
- Files you drop on the window or right-click keep 1.x's behaviour (renamed where they are);
  only files in a watched folder are moved into the library folders.

## Left to the builder

- How often to look at the watched folders, and the exact quiet time for the summary.
- Which temporary-file names Download Station uses (check on the NAS during the owner's test).
- Module names and how the canvas list draws bands.
