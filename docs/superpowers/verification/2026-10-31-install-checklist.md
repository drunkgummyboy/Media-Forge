# Install and update checklist (for the owner)

Tests cover the installer, the updater and the uninstaller with temporary folders and a test
copy of the "Installed apps" entry. These checks need a real PC, ideally one without Python (a
spare laptop or a virtual machine), and can only be done after MediaForge is on GitHub.

## 1. Install

1. Make the short link: at is.gd, shorten
   `https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/main/install.ps1` with the
   custom name `mediaforge`.
2. Open **Windows PowerShell**, paste `irm is.gd/mediaforge | iex`, press Enter.
3. Expect: it installs Python if needed, then says **MediaForge is ready** and MediaForge opens.
4. Check:
   - MediaForge is in the Start menu and on the desktop.
   - **Settings → Apps → Installed apps** lists MediaForge 1.1.0 (publisher drunkgummyboy).
   - Right-click a video file in Explorer (Windows 11: **Show more options**): a **MediaForge**
     submenu with one entry per preset. Pick one: the confirm window opens.
5. On your own PC, first start: in the welcome window's step 1, pick your old
   `mediaforge_config.json` (in the old MediaForge folder) to bring over your TMDB key and presets.

## 2. Update

1. On the development PC: change `__version__` in `src/mediaforge/__init__.py` to `1.1.1`,
   commit and push (ask Claude to do this).
2. On the test PC: start MediaForge and wait about 10 seconds.
3. Expect: a bar above the Activity strip, **MediaForge 1.1.1 is ready. It installs when you
   close MediaForge.**
4. Close MediaForge and start it again: **Settings → About** shows MediaForge 1.1.1, and
   Installed apps shows 1.1.1.
5. Also try **Settings → About → Check for updates** with automatic updates switched off.

## 3. Reinstall

Paste the install line again: it finishes with **MediaForge is ready**, and your presets and
history are still there.

## 4. Uninstall

1. **Settings → Apps → Installed apps → MediaForge → Uninstall**. Answer **No** to "Also delete
   your settings and history?".
2. Expect: the shortcuts, the right-click submenu and the Installed-apps entry are gone;
   `%APPDATA%\MediaForge` is still there.
3. Install again, then uninstall answering **Yes**: `%APPDATA%\MediaForge` and
   `%LOCALAPPDATA%\MediaForge` are gone too.

## Still from the rewrite

- A real rename on the NAS share.
- A real trailer download.
