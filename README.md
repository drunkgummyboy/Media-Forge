# MediaForge

Clean names for your video files.

A Windows desktop app that renames messy release-scene video filenames into clean, standard names
and folders (`Dune Part Two (2024).mkv`, `VisionQuest/Season 01/VisionQuest - S01E02 - Theseus.mkv`),
using TMDB for official titles, years, episode names and posters, and yt-dlp for trailers. It shares
AirGrabber's look: light or dark, ten accent colours, shows and movies in bands. You see
every new name before anything changes, and you can undo any of your last 50 runs, whole or file by
file.

## Install

On Windows 10 or 11:

1. Click **Start**, type **powershell** and open **Windows PowerShell**.
2. Copy this line, paste it into the blue window (Ctrl+V or right-click) and press **Enter**:

   ```
   irm is.gd/MediaForge | iex
   ```

3. Wait until it says **MediaForge is ready**.

MediaForge then appears in the Start menu and on your desktop, in Explorer's right-click menu for
files and folders (on Windows 11 under **Show more options**), and opens by itself. If you don't
have Python yet (the program MediaForge runs on), the installer adds it for you. Nothing needs
administrator permission.

On first start, MediaForge asks for a free TMDB API key (from themoviedb.org) and can bring over the
settings of the old single-file MediaForge.

Only paste commands like this from sources you trust. This one downloads MediaForge's installer,
[`install.ps1`](install.ps1), from this page; you can read it first. `is.gd/MediaForge` is a short
link to it; the same command with the full address is:

```
irm https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/main/install.ps1 | iex
```

## Update

MediaForge updates itself: a new version downloads in the background and installs when you close
MediaForge. You can also check under **Settings → About → Check for updates**, or turn automatic
updates off there. Your settings and history are kept.

## Uninstall

Open Windows **Settings → Apps → Installed apps**, find **MediaForge** and choose **Uninstall**.

Windows then asks whether to **also delete your settings and history** (your presets, TMDB key and
the list of past renames used for undo):

- Answer **No** to keep them. If you install MediaForge again later, everything is still there.
- Answer **Yes** only if you want them gone for good.

Python stays installed; remove it the same way if nothing else needs it.

## If MediaForge stops starting

Paste the install line again. It reinstalls the latest version and repairs MediaForge's own
Python environment; your settings and history are kept.

## The old single-file MediaForge

`MediaForge.py` (and the older `MediaForge_v0_4.py` / `MediaForge_v0_5.py`) is the old single-file
version. It stays here as the reference the new version was built from.

## Support

MediaForge is free. If it saves you time, you can buy me a coffee on
[Ko-fi](https://ko-fi.com/airgrabber). Thank you!

## Development

```powershell
python main.py                     # run from a checkout (never updates itself)
python -m pytest                   # tests
python -m ruff check src tests     # lint
python -m pyright                  # strict type check (see CLAUDE.md for the exact command)
```

A checkout (anything with a `tests` folder) never updates or uninstalls itself; see `CLAUDE.md`
for the project rules.

MediaForge is MIT-licensed (see [LICENSE](LICENSE)). It uses guessit and rebulk (LGPL), yt-dlp
(Unlicense) and FFmpeg (GPL), which are downloaded separately.
