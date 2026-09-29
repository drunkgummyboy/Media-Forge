# ==============================================================================
# SECTION 1: IMPORTS & SETUP
# ==============================================================================
from __future__ import annotations

import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor


# --- AUTO-INSTALLER ---
def _auto_install_deps():
    reqs = {
        "customtkinter": "customtkinter",
        "tkinterdnd2": "tkinterdnd2",
        "pillow": "PIL",
        "requests": "requests",
        "yt-dlp": "yt_dlp",
        "imageio-ffmpeg": "imageio_ffmpeg",
    }

    to_install = []
    for pip_name, import_name in reqs.items():
        try:
            __import__(import_name)
        except ImportError:
            to_install.append(pip_name)

    # STEP 1: Install any completely missing dependencies first
    if to_install:
        print(
            f"[*] Missing dependencies found. Auto-installing: {', '.join(to_install)}..."
        )
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *to_install])
            print("[*] Dependencies installed successfully!")
            import site
            import importlib

            importlib.reload(site)
        except Exception as e:
            print(f"[!] Failed to auto-install dependencies: {e}")
            print(
                "[!] Please install them manually using: pip install "
                + " ".join(to_install)
            )
            sys.exit(1)

    # STEP 2: Make sure pip itself is current
    print("[*] Checking for pip updates...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[!] Could not check for pip updates (Offline?): {e}")

    # STEP 3: Silently check for and install updates for all required packages
    print("[*] Checking for package updates (this may take a few seconds)...")
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--quiet",
                *reqs.keys(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[!] Could not check for updates (Offline?): {e}")


_auto_install_deps()
# ----------------------

import argparse
import datetime as _dt
import os
import queue
import re
import shlex
import threading
import unicodedata
import webbrowser
import io
import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple
from functools import lru_cache

# --- GUI & Drag-and-Drop Imports ---
import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES

# --- Optional imports ---
try:
    from PIL import Image, ImageTk  # type: ignore

    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

try:
    import requests  # type: ignore

    _HAVE_REQUESTS = True
except Exception:
    _HAVE_REQUESTS = False

try:
    import imageio_ffmpeg  # type: ignore

    _HAVE_FFMPEG_BIN = True
except Exception:
    _HAVE_FFMPEG_BIN = False

# --- REBRANDING & GLOBALS ---
APP_TITLE = "MediaForge"
CONFIG_FILE = Path(__file__).parent / "mediaforge_config.json"
UNDO_FILE = Path(__file__).parent / "mediaforge_undo.json"
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv"}

ASSET_URLS = {
    "MediaForge.png": "https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/refs/heads/main/MediaForge.png",
    "MediaForge.ico": "https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/refs/heads/main/MediaForge.ico",
}


def _ensure_assets(script_dir: Path):
    if not _HAVE_REQUESTS:
        return
    for filename, url in ASSET_URLS.items():
        dest = script_dir / filename
        if dest.exists():
            continue
        try:
            resp = requests.get(url, timeout=6)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            print(f"[*] Downloaded missing asset: {filename}")
        except Exception as e:
            print(f"[!] Could not download asset {filename}: {e}")


FORBIDDEN = set('<>:"/\\|?*') | {chr(c) for c in range(0, 32)}
SPACE_RE = re.compile(r"\s+")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)")

EP2_SUFFIX_STD = r"(?:(?:[.\s]*(?:-|&|\+|and|E|Ep|Episode))+[.\s]*(?!(?:720|1080|264|265|480|2160)(?!\d))(\d{1,3})(?!\d|[pPiI]))?"
EP2_SUFFIX_X = r"(?:(?:[.\s]*(?:-|&|\+|and|x|X))+[.\s]*(?!(?:720|1080|264|265|480|2160)(?!\d))(\d{1,3})(?!\d|[pPiI]))?"

SERIES_RE = re.compile(
    r"(?i)(?<![a-z])(?:S|Season)[.\s]*(\d{1,4})[.\s-]*(?:E|Ep|Episode)[.\s]*(\d{1,4})"
    + EP2_SUFFIX_STD
    + r"(?![a-z])"
)
SERIES_X_RE = re.compile(
    r"(?i)(?<!\d)(\d{1,4})[.\s]*[xX][.\s]*(\d{1,4})" + EP2_SUFFIX_X + r"(?!\d)"
)
EP_ONLY_RE = re.compile(
    r"(?i)(?<![a-z])(?:Ep(?:isode)?|E)[.\s]*(\d{1,3})" + EP2_SUFFIX_STD + r"(?![a-z])"
)

GROUP_SUFFIX_RE = re.compile(r"\s*-\s*[A-Za-z0-9]+$")
BRACKETS_RE = re.compile(r"[\[\(]([^\]\)]{0,128})[\]\)]")

TAG_PATTERNS = {
    "resolution": re.compile(r"(?i)\b(?:480p|720p|1080p|2160p|4k|8k)\b"),
    "source": re.compile(
        r"(?i)\b(?:bluray|b[dr]rip|web[-.]?dl|webrip|web|hdrip|hdtv|dvdrip)\b"
    ),
    "codec": re.compile(r"(?i)\b(?:x264|x265|h\.?264|h\.?265|hevc|av1)\b"),
    "audio": re.compile(r"(?i)\b(?:dts\.?hd|dts|aac|ac3|truehd|atmos)\b"),
    "edition": re.compile(
        r"(?i)\b(?:director'?s cut|extended|unrated|remastered|imax|criterion)\b"
    ),
}


# ==============================================================================
# SECTION 2: DATA MODEL & UTILS
# ==============================================================================
@dataclass
class ParsedName:
    title: str
    year: str
    se_tag: str
    extra: str
    season: Optional[int] = None
    episode: Optional[int] = None
    episode2: Optional[int] = None
    episode_title: str = ""


def is_video_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTS


def collapse_spaces(s: str) -> str:
    return SPACE_RE.sub(" ", s).strip()


def sanitize_segment(seg: str) -> str:
    seg = unicodedata.normalize("NFKC", seg)
    seg = "".join(ch for ch in seg if ch not in FORBIDDEN)
    seg = collapse_spaces(seg)
    seg = seg.strip(" .")
    return seg or "_"


def capitalize_human(token: str) -> str:
    if not token:
        return token
    if token.isupper():
        return token
    return token[:1].upper() + token[1:].lower()


def human_title(s: str) -> str:
    parts = [capitalize_human(p) for p in s.split()]
    return " ".join(parts) or "_"


def _extract_series_bits(
    text: str,
) -> Tuple[Optional[int], Optional[int], Optional[int], str, str, str]:
    m = SERIES_RE.search(text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        e2 = int(m.group(3)) if m.group(3) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return (
            s,
            e,
            e2,
            se,
            text[: m.start()].strip(" -_."),
            text[m.end() :].strip(" -_."),
        )

    m = SERIES_X_RE.search(text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        e2 = int(m.group(3)) if m.group(3) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return (
            s,
            e,
            e2,
            se,
            text[: m.start()].strip(" -_."),
            text[m.end() :].strip(" -_."),
        )

    m = EP_ONLY_RE.search(text)
    if m:
        s, e = 1, int(m.group(1))
        e2 = int(m.group(2)) if m.group(2) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return (
            s,
            e,
            e2,
            se,
            text[: m.start()].strip(" -_."),
            text[m.end() :].strip(" -_."),
        )

    return None, None, None, "", text.strip(" -_."), ""


def parse_filename(path: Path, is_series: bool) -> ParsedName:
    stem = path.stem
    bracket_bits = [m.group(1) for m in BRACKETS_RE.finditer(stem)]
    stem = BRACKETS_RE.sub(" ", stem)
    s = collapse_spaces(stem.replace(".", " ").replace("_", " "))

    season, episode, episode2, se_tag = None, None, None, ""
    ep_title_raw = ""

    ss, ee, ee2, se, left_part, right_part = _extract_series_bits(s)
    if ss is not None and ee is not None:
        season, episode, episode2, se_tag = ss, ee, ee2, se
        s = collapse_spaces(left_part)
        ep_title_raw = right_part

    while True:
        new_s = GROUP_SUFFIX_RE.sub("", s)
        if new_s == s:
            break
        s = collapse_spaces(new_s)

    while True:
        new_ep = GROUP_SUFFIX_RE.sub("", ep_title_raw)
        if new_ep == ep_title_raw:
            break
        ep_title_raw = collapse_spaces(new_ep)

    year, right = "", ""
    for b in list(bracket_bits):
        m_year = YEAR_RE.search(b)
        if m_year:
            year = m_year.group(1)
            b_clean = YEAR_RE.sub("", b).strip(" -_.")
            if b_clean:
                bracket_bits[bracket_bits.index(b)] = b_clean
            else:
                bracket_bits.remove(b)
            break

    if not year:
        m = YEAR_RE.search(s)
        if m:
            year = m.group(1)
            left = s[: m.start()].strip(" -_.")
            right = s[m.end() :].strip(" -_.")
        else:
            left = s.strip(" -_.")
            right = ""
    else:
        left = s.strip(" -_.")
        right = ""

    extras_set, seen = [], set()

    def add_extra(x: str):
        k = x.lower()
        if k and k not in seen:
            extras_set.append(x)
            seen.add(k)

    updated_tag_patterns = TAG_PATTERNS.copy()
    updated_tag_patterns["audio"] = re.compile(
        r"(?i)\b(?:dts\.?hd|dts|aac|ac3|truehd|atmos|dd5\.?1|dd5)\b"
    )

    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(right):
            add_extra(collapse_spaces(hit))

    left_clean = left
    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(left_clean):
            add_extra(collapse_spaces(hit))
        left_clean = rgx.sub(" ", left_clean)
    left_clean = collapse_spaces(left_clean)

    ep_title_clean = ep_title_raw
    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(ep_title_clean):
            add_extra(collapse_spaces(hit))
        ep_title_clean = rgx.sub(" ", ep_title_clean)
    ep_title_clean = collapse_spaces(ep_title_clean)

    title_raw = left_clean or left or "_"
    for b in bracket_bits:
        add_extra(collapse_spaces(b))
    extra_line = " • ".join([se_tag] + extras_set).strip(" •")

    return ParsedName(
        title=human_title(title_raw) or "_",
        year=year,
        se_tag=se_tag,
        extra=extra_line,
        season=season,
        episode=episode,
        episode2=episode2,
        episode_title=human_title(ep_title_clean),
    )


# ==============================================================================
# SECTION 3: TMDB CLIENT
# ==============================================================================
class TMDBClient:
    BASE = "https://api.themoviedb.org/3"
    IMG_BASE = "https://image.tmdb.org/t/p"

    def __init__(
        self, api_key: str, language: Optional[str] = None, timeout: float = 10.0
    ):
        self.api_key = api_key
        self.language = language
        self.timeout = timeout
        self._session = requests.Session() if _HAVE_REQUESTS else None

    @lru_cache(maxsize=256)
    def _request_cached(self, path: str, params_tuple: tuple) -> dict:
        params = dict(params_tuple)
        params = {**params, "api_key": self.api_key, "include_adult": "false"}
        if self.language:
            params.setdefault("language", self.language)

        url = f"{self.BASE}{path}"
        if _HAVE_REQUESTS:
            r = self._session.get(url, params=params, timeout=self.timeout)  # type: ignore
            r.raise_for_status()
            return r.json()

        from urllib.parse import urlencode
        from urllib.request import urlopen

        full = f"{url}?{urlencode(params)}"
        with urlopen(full, timeout=self.timeout) as resp:  # type: ignore
            return json.loads(resp.read().decode("utf-8"))

    def _request(self, path: str, params: dict) -> dict:
        return self._request_cached(path, tuple(sorted(params.items())))

    def search_movie_all(self, query: str, year: Optional[str]) -> List[dict]:
        results = []
        tries = [{"query": query, "year": year}] if year else []
        tries.append({"query": query})

        for params in tries:
            data = self._request(
                "/search/movie", {k: v for k, v in params.items() if v}
            )
            results.extend(data.get("results") or [])

        seen, uniq = set(), []
        for r in results:
            if r.get("id") not in seen:
                uniq.append(r)
                seen.add(r.get("id"))
        return uniq

    def search_tv_all(self, query: str, year: Optional[str] = None) -> List[dict]:
        params = {"query": query}
        if year:
            params["first_air_date_year"] = year
        return self._request("/search/tv", params).get("results") or []

    def tv_details(self, tv_id: int) -> dict:
        return self._request(f"/tv/{tv_id}", {})

    def season_details(self, tv_id: int, season_number: int) -> dict:
        return self._request(f"/tv/{tv_id}/season/{season_number}", {})

    def poster_url(self, poster_path: str, size: str = "w500") -> str:
        return f"{self.IMG_BASE}/{size}{poster_path}"

    def fetch_bytes(self, url: str) -> bytes:
        if _HAVE_REQUESTS:
            r = self._session.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.content
        from urllib.request import urlopen

        with urlopen(url, timeout=self.timeout) as resp:
            return resp.read()


def confident_tmdb_match(
    results: list, title: str, year: Optional[str]
) -> Optional[dict]:
    if not results or not year:
        return None
    norm_title = title.strip().lower()
    for r in results:
        r_title = (r.get("title") or r.get("name") or "").strip().lower()
        date = r.get("release_date") or r.get("first_air_date") or ""
        if r_title == norm_title and date[:4] == year:
            return r
    return None


# ==============================================================================
# SECTION 4: TRAILER & POSTER DOWNLOADER
# ==============================================================================
def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _download_trailer_with_module(url: str, outtmpl: str) -> bool:
    try:
        import yt_dlp  # type: ignore

        ydl_opts = {
            "outtmpl": outtmpl,
            "quiet": True,
            "noprogress": True,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "extractor_args": {"youtube": {"client": ["tv", "mweb", "ios"]}},
        }
        if _HAVE_FFMPEG_BIN:
            import imageio_ffmpeg

            ydl_opts["ffmpeg_location"] = imageio_ffmpeg.get_ffmpeg_exe()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"[yt-dlp error] {e}")
        return False


def _download_trailer_with_binary(url: str, outtmpl: str) -> bool:
    try:
        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "--extractor-args",
            "youtube:client=tv,mweb,ios",
            "-o",
            outtmpl,
        ]
        if _HAVE_FFMPEG_BIN:
            import imageio_ffmpeg

            cmd.extend(["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()])
        cmd.append(url)

        subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        return True
    except Exception as e:
        print(f"[yt-dlp binary error] {e}")
        return False


def download_trailer(url: str, dest_noext: Path) -> Tuple[bool, Path]:
    _ensure_dir(dest_noext.parent)
    outtmpl = str(dest_noext) + ".%(ext)s"
    if _download_trailer_with_module(url, outtmpl) or _download_trailer_with_binary(
        url, outtmpl
    ):
        # Case-insensitive/glob fallback to find the file yt-dlp actually created
        for f in dest_noext.parent.iterdir():
            if f.is_file() and f.stem == dest_noext.name:
                return True, f
        return True, Path(str(dest_noext) + ".mp4")

    url_file = Path(str(dest_noext) + ".url")
    url_file.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8")
    return False, url_file


# ==============================================================================
# SECTION 5: FILE SYSTEM OPERATIONS
# ==============================================================================
JUNK_FILENAMES = {"Thumbs.db", ".DS_Store", "desktop.ini"}
POSTER_RE = re.compile(r"^(?P<base>.+) - Poster\.jpg$", re.IGNORECASE)
SERIES_POSTER_RE = re.compile(r"^(?P<base>.+) - Series Poster\.jpg$", re.IGNORECASE)
SEASON_POSTER_RE = re.compile(r"^Season \d{2} Poster\.jpg$", re.IGNORECASE)


def _has_matching_video(dirpath: Path, base_stem: str) -> bool:
    for ext in VIDEO_EXTS:
        if (dirpath / f"{base_stem}{ext}").exists():
            return True
    return False


def fast_rglob(root_dir: Path) -> Iterator[Path]:
    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                if entry.is_file():
                    yield Path(entry.path)
                elif entry.is_dir():
                    yield from fast_rglob(Path(entry.path))
    except PermissionError:
        pass


def _dir_has_videos(root: Path) -> bool:
    for f in fast_rglob(root):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            return True
    return False


def clean_artifacts(root: Path, dry_run: bool) -> tuple[int, int, int]:
    junk = orphan_posters = url_cleaned = 0
    if not root.exists():
        return (0, 0, 0)

    for p in fast_rglob(root):
        name = p.name
        lower_suffix = p.suffix.lower()
        try:
            if name in JUNK_FILENAMES:
                if not dry_run:
                    p.unlink(missing_ok=True)
                junk += 1
                continue

            if lower_suffix == ".url":
                base = p.stem
                if _has_matching_video(p.parent, base):
                    if not dry_run:
                        p.unlink(missing_ok=True)
                    url_cleaned += 1
                continue

            if lower_suffix == ".jpg":
                m = POSTER_RE.match(p.name)
                if m and not _has_matching_video(p.parent, m.group("base")):
                    if not dry_run:
                        p.unlink(missing_ok=True)
                    orphan_posters += 1
                    continue

                m_series = SERIES_POSTER_RE.match(p.name)
                if m_series:
                    if not _dir_has_videos(p.parent):
                        if not dry_run:
                            p.unlink(missing_ok=True)
                        orphan_posters += 1
                    continue

                if SEASON_POSTER_RE.match(p.name):
                    if not any(
                        True
                        for _ in (
                            f
                            for f in p.parent.iterdir()
                            if f.is_file() and f.suffix.lower() in VIDEO_EXTS
                        )
                    ):
                        if not dry_run:
                            p.unlink(missing_ok=True)
                        orphan_posters += 1
        except Exception as ex:
            print(f"[CLEAN WARN] {p}: {ex}")

    return junk, orphan_posters, url_cleaned


# ==============================================================================
# SECTION 6: GUI GLOBALS & SETUP
# ==============================================================================
PALETTES = {
    "Dark": {
        "BG_MAIN": "#223038",
        "BG_SIDEBAR": "#1A262B",
        "BG_CARD": "#2B3C45",
        "TEAL_PRIMARY": "#3A7D90",
        "TEAL_HOVER": "#2F6676",
        "ORANGE_ACCENT": "#E86A33",
        "ORANGE_HOVER": "#CF5A27",
        "RED_CLEAR": "#D9534F",
        "RED_CLEAR_HOVER": "#C9302C",
        "TEXT_LIGHT": "#E0E6ED",
        "TEXT_MUTED": "#8A97A0",
        "GREEN_SUCCESS": "#4CAF50",
    },
    "Light": {
        "BG_MAIN": "#F2F4F6",
        "BG_SIDEBAR": "#FFFFFF",
        "BG_CARD": "#E3E8EC",
        "TEAL_PRIMARY": "#2E7A90",
        "TEAL_HOVER": "#256575",
        "ORANGE_ACCENT": "#D9622C",
        "ORANGE_HOVER": "#C25422",
        "RED_CLEAR": "#C94A44",
        "RED_CLEAR_HOVER": "#B33F3A",
        "TEXT_LIGHT": "#1C242A",
        "TEXT_MUTED": "#5C6873",
        "GREEN_SUCCESS": "#2E7D32",
    },
}


def _load_saved_theme_pref() -> str:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            pref = data.get("theme", "Dark")
            if pref in PALETTES or pref == "System":
                return pref
    except Exception:
        pass
    return "Dark"


def _resolve_effective_mode(pref: str) -> str:
    ctk.set_appearance_mode(pref)
    return ctk.get_appearance_mode()


def _apply_palette(mode: str):
    global \
        BG_MAIN, \
        BG_SIDEBAR, \
        BG_CARD, \
        TEAL_PRIMARY, \
        TEAL_HOVER, \
        ORANGE_ACCENT, \
        ORANGE_HOVER, \
        RED_CLEAR, \
        RED_CLEAR_HOVER, \
        TEXT_LIGHT, \
        TEXT_MUTED, \
        GREEN_SUCCESS
    p = PALETTES.get(mode, PALETTES["Dark"])
    BG_MAIN, BG_SIDEBAR, BG_CARD = p["BG_MAIN"], p["BG_SIDEBAR"], p["BG_CARD"]
    TEAL_PRIMARY, TEAL_HOVER = p["TEAL_PRIMARY"], p["TEAL_HOVER"]
    ORANGE_ACCENT, ORANGE_HOVER = p["ORANGE_ACCENT"], p["ORANGE_HOVER"]
    RED_CLEAR, RED_CLEAR_HOVER = p["RED_CLEAR"], p["RED_CLEAR_HOVER"]
    TEXT_LIGHT, TEXT_MUTED = p["TEXT_LIGHT"], p["TEXT_MUTED"]
    GREEN_SUCCESS = p["GREEN_SUCCESS"]


_THEME_PREF = _load_saved_theme_pref()
_EFFECTIVE_THEME_MODE = _resolve_effective_mode(_THEME_PREF)
_apply_palette(_EFFECTIVE_THEME_MODE)

DEFAULT_PRESETS = {
    "Auto": {"type": "auto", "format": "{ny}", "chk": []},
    "Movie (Folder)": {
        "type": "movie",
        "format": "{ny}/{ny}",
        "chk": ["chk_poster", "chk_trailer"],
    },
    "Movie (Flat)": {"type": "movie", "format": "{ny}", "chk": []},
    "Series (Folder)": {
        "type": "tv",
        "format": "{ny}/{ny} - Season {s}/{ny} - {s00e00} - {en}",
        "chk": ["chk_series_poster", "chk_season_poster"],
    },
    "Series (Flat)": {"type": "tv", "format": "{ny} - {s00e00} - {en}", "chk": []},
}

CHK_MAPPING = {
    "chk_poster": "Movie Poster",
    "chk_series_poster": "Series Poster",
    "chk_season_poster": "Season Posters",
    "chk_trailer": "Download Trailer",
    "chk_clean": "Clean Junk Files",
    "chk_prune": "Prune Folders",
    "chk_recursive": "Recursive Scan",
}

CHK_GROUPS = {
    "Metadata": ["chk_poster", "chk_series_poster", "chk_season_poster", "chk_trailer"],
    "Cleanup & Scan": ["chk_clean", "chk_prune", "chk_recursive"],
}

STATUS_ICONS = {"done": "✅", "error": "⚠️", "pending": "⏳"}


class CustomDnDApp(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


# ==============================================================================
# SECTION 7: DIALOG WINDOWS
# ==============================================================================


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Settings & Presets")
        self.geometry("480x780")
        self.minsize(420, 480)
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(0, weight=1)

        self.preset_type = ctk.StringVar(value="auto")
        self.chk_vars = {k: ctk.BooleanVar(value=False) for k in CHK_MAPPING.keys()}

        theme_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        theme_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        theme_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            theme_frame,
            text="Theme",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")
        self.theme_var = ctk.StringVar(value=self.parent.config.get("theme", "Dark"))
        self.theme_menu = ctk.CTkOptionMenu(
            theme_frame,
            values=["Dark", "Light", "System"],
            variable=self.theme_var,
            fg_color=BG_MAIN,
            button_color=TEAL_PRIMARY,
            button_hover_color=TEAL_HOVER,
            command=self.change_theme,
        )
        self.theme_menu.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="w")
        ctk.CTkLabel(
            theme_frame,
            text="Restart MediaForge to fully apply a new theme.",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=1, padx=10, pady=(0, 10), sticky="w")

        api_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        api_frame.grid(row=1, column=0, padx=20, pady=(10, 10), sticky="ew")
        api_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            api_frame,
            text="TMDB API Key",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")

        api_inner = ctk.CTkFrame(api_frame, fg_color="transparent")
        api_inner.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        api_inner.grid_columnconfigure(0, weight=1)

        self.api_entry = ctk.CTkEntry(
            api_inner,
            placeholder_text="Enter TMDB API Key...",
            border_color=TEAL_PRIMARY,
        )
        self.api_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.api_entry.insert(0, self.parent.tmdb_api_key)

        ctk.CTkButton(
            api_inner,
            text="Save Key",
            width=80,
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.save_api_key,
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            self,
            text="Manage Presets",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEAL_PRIMARY,
        ).grid(row=2, column=0, pady=(15, 5))

        self.preset_selector = ctk.CTkOptionMenu(
            self,
            values=list(self.parent.presets.keys()),
            width=350,
            fg_color=BG_CARD,
            button_color=TEAL_PRIMARY,
            button_hover_color=TEAL_HOVER,
            command=self.load_preset_to_ui,
        )
        self.preset_selector.grid(row=3, column=0, pady=10)

        form_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        form_frame.grid(row=4, column=0, padx=20, pady=10, sticky="nsew")
        form_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form_frame, text="Preset Name:").grid(
            row=0, column=0, padx=10, pady=10, sticky="w"
        )
        self.name_entry = ctk.CTkEntry(form_frame, border_color=TEAL_PRIMARY)
        self.name_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(form_frame, text="Media Type:").grid(
            row=1, column=0, padx=10, pady=10, sticky="w"
        )
        self.type_seg = ctk.CTkSegmentedButton(
            form_frame,
            values=["auto", "movie", "tv"],
            variable=self.preset_type,
            selected_color=TEAL_PRIMARY,
            selected_hover_color=TEAL_HOVER,
        )
        self.type_seg.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(form_frame, text="Format:").grid(
            row=2, column=0, padx=10, pady=10, sticky="w"
        )
        self.format_entry = ctk.CTkEntry(form_frame, border_color=TEAL_PRIMARY)
        self.format_entry.grid(row=2, column=1, padx=10, pady=10, sticky="ew")

        mods_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        mods_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(
            mods_frame, text="Active Modifiers:", text_color=ORANGE_ACCENT
        ).pack(anchor="w", pady=(0, 5))

        chk_container = ctk.CTkFrame(mods_frame, fg_color="transparent")
        chk_container.pack(fill="x")
        chk_container.grid_columnconfigure(0, weight=1)
        chk_container.grid_columnconfigure(1, weight=1)

        row_idx, col_idx = 0, 0
        for k, label_text in CHK_MAPPING.items():
            cb = ctk.CTkCheckBox(
                chk_container,
                text=label_text,
                variable=self.chk_vars[k],
                fg_color=ORANGE_ACCENT,
                hover_color=ORANGE_HOVER,
            )
            cb.grid(row=row_idx, column=col_idx, sticky="w", pady=5)
            col_idx += 1
            if col_idx > 1:
                col_idx = 0
                row_idx += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, pady=10)

        ctk.CTkButton(
            btn_frame,
            text="💾 Save / Overwrite",
            width=140,
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.save_preset,
        ).grid(row=0, column=0, padx=10)
        ctk.CTkButton(
            btn_frame,
            text="🗑️ Delete Preset",
            width=140,
            fg_color=RED_CLEAR,
            hover_color=RED_CLEAR_HOVER,
            command=self.delete_preset,
        ).grid(row=0, column=1, padx=10)

        # --- SYSTEM INTEGRATION UI ---
        integration_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        integration_frame.grid(row=6, column=0, padx=20, pady=(10, 20), sticky="ew")
        integration_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            integration_frame,
            text="System Integration",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

        ctk.CTkLabel(
            integration_frame,
            text="Generates a .reg file to add MediaForge to your right-click context menu.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            wraplength=400,
            justify="left",
        ).grid(row=1, column=0, padx=10, pady=(0, 10), sticky="w")

        ctk.CTkButton(
            integration_frame,
            text="🔧 Generate Context Menu Registry File",
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.generate_registry_file,
        ).grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")

        if self.parent.presets:
            first_key = list(self.parent.presets.keys())[0]
            self.preset_selector.set(first_key)
            self.load_preset_to_ui(first_key)

    def save_api_key(self):
        key = self.api_entry.get().strip()

        try:
            test_client = TMDBClient(api_key=key, timeout=5.0)
            test_client._request("/configuration", {})
        except Exception as e:
            messagebox.showerror(
                "Authentication Failed", f"TMDB rejected this API key.\n\nDetails: {e}"
            )
            return

        self.parent.tmdb_api_key = key
        os.environ["TMDB_API_KEY"] = key
        self.parent.save_config()
        messagebox.showinfo("Saved", "API Key has been saved successfully.")

    def change_theme(self, choice):
        self.parent.config["theme"] = choice
        self.parent.save_config()
        messagebox.showinfo(
            "Theme Saved",
            f"Theme set to '{choice}'.\n\nRestart MediaForge for the new theme to fully apply everywhere.",
        )

    def load_preset_to_ui(self, name):
        data = self.parent.presets.get(name)
        if not data:
            return
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, name)
        self.format_entry.delete(0, "end")
        self.format_entry.insert(0, data.get("format", ""))
        self.preset_type.set(data.get("type", "auto"))

        active_chks = data.get("chk", [])
        for k, var in self.chk_vars.items():
            var.set(k in active_chks)

    def save_preset(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Preset name cannot be empty.")
            return

        active_chks = [k for k, var in self.chk_vars.items() if var.get()]

        self.parent.presets[name] = {
            "type": self.preset_type.get(),
            "format": self.format_entry.get().strip(),
            "chk": active_chks,
        }

        self.parent.save_config()
        self.parent.refresh_preset_ui(name)
        self.preset_selector.configure(values=list(self.parent.presets.keys()))
        self.preset_selector.set(name)
        messagebox.showinfo("Saved", f"Preset '{name}' has been saved.")

    def delete_preset(self):
        name = self.name_entry.get().strip()
        if name not in self.parent.presets:
            messagebox.showerror("Error", "Preset not found.")
            return
        if len(self.parent.presets) <= 1:
            messagebox.showerror(
                "Error", "You cannot delete the last remaining preset."
            )
            return

        del self.parent.presets[name]
        self.parent.save_config()

        new_default = list(self.parent.presets.keys())[0]
        self.parent.refresh_preset_ui(new_default)
        self.preset_selector.configure(values=list(self.parent.presets.keys()))
        self.preset_selector.set(new_default)
        self.load_preset_to_ui(new_default)

    def generate_registry_file(self):
        # 1. Create a popup dialog to select presets
        popup = ctk.CTkToplevel(self)
        popup.title("Select Context Menu Presets")
        popup.geometry("380x450")
        popup.attributes("-topmost", True)
        popup.configure(fg_color=BG_MAIN)
        popup.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            popup,
            text="Include Presets in Right-Click Menu:",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, pady=(20, 10), padx=20, sticky="w")

        # 2. Scrollable list of available presets
        scroll = ctk.CTkScrollableFrame(popup, fg_color=BG_CARD, corner_radius=8)
        scroll.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        popup.grid_rowconfigure(1, weight=1)

        chk_vars = {}
        for preset_name in self.parent.presets.keys():
            var = ctk.BooleanVar(value=True)  # Default to selected
            chk_vars[preset_name] = var
            cb = ctk.CTkCheckBox(
                scroll,
                text=preset_name,
                variable=var,
                fg_color=ORANGE_ACCENT,
                hover_color=ORANGE_HOVER,
                text_color=TEXT_LIGHT,
            )
            cb.pack(anchor="w", pady=8, padx=10)

        # 3. Inner function that triggers when "Save" is clicked
        def on_generate():
            selected = [name for name, var in chk_vars.items() if var.get()]
            if not selected:
                messagebox.showerror(
                    "Error", "Please select at least one preset.", parent=popup
                )
                return

            python_exe = sys.executable
            if python_exe.lower().endswith("python.exe"):
                python_exe = python_exe[:-10] + "pythonw.exe"

            script_path = Path(__file__).resolve()
            icon_path = script_path.parent / "MediaForge.ico"

            py_reg = str(python_exe).replace("\\", "\\\\")
            script_reg = str(script_path).replace("\\", "\\\\")
            icon_reg = str(icon_path).replace("\\", "\\\\")

            subcommands = []
            command_stores = []

            # 4. Dynamically build the registry blocks for each chosen preset
            for preset in selected:
                # Strip spaces/special chars to create a safe, valid registry key name
                safe_key = "MediaForge_" + re.sub(r"[^a-zA-Z0-9]", "", preset)
                subcommands.append(safe_key)

                cmd_block = (
                    f"[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\CommandStore\\shell\\{safe_key}]\n"
                    f'@="Forge: {preset}"\n'
                    f"[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\CommandStore\\shell\\{safe_key}\\command]\n"
                    f'@="\\"{py_reg}\\" \\"{script_reg}\\" \\"%1\\" --preset \\"{preset}\\" --auto-run"\n'
                )
                command_stores.append(cmd_block)

            subcommands_str = ";".join(subcommands)
            stores_str = "\n".join(command_stores)

            reg_content = f"""Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SOFTWARE\\Classes\\*\\shell\\MediaForgeCommands]
"MUIVerb"="MediaForge"
"SubCommands"="{subcommands_str}"
"Icon"="{icon_reg}"

[HKEY_LOCAL_MACHINE\\SOFTWARE\\Classes\\Folder\\shell\\MediaForgeCommands]
"MUIVerb"="MediaForge"
"SubCommands"="{subcommands_str}"
"Icon"="{icon_reg}"

{stores_str}
"""
            # Use Path.home() to dynamically find the current user's Desktop
            reg_file_path = Path.home() / "Desktop" / "MediaForge_Context_Install.reg"
            try:
                with open(reg_file_path, "w", encoding="utf-8") as f:
                    f.write(reg_content)
                    messagebox.showinfo(
                        "Registry File Generated",
                        f"Successfully created:\n{reg_file_path.name} (on your Desktop)\n\n"
                        f"Double-click this file on your Desktop to update your context menu.",
                        parent=popup,
                    )

                popup.destroy()
            except Exception as e:
                messagebox.showerror(
                    "Error", f"Failed to generate registry file:\n{e}", parent=popup
                )

        btn = ctk.CTkButton(
            popup,
            text="💾 Generate .reg File",
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=on_generate,
        )
        btn.grid(row=2, column=0, pady=20)


class MatchPickerDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        results: list,
        is_tv: bool,
        file_name: str,
        choice_holder: dict,
        event: "threading.Event",
        tmdb_client: "TMDBClient",
    ):
        super().__init__(parent)
        self.parent = parent
        self.choice_holder = choice_holder
        self.event = event
        self.tmdb_client = tmdb_client
        self._thumb_labels: Dict[int, ctk.CTkLabel] = {}

        self.title("Confirm TMDB Match")
        self.geometry("480x580")
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        kind = "TV Show" if is_tv else "Movie"
        ctk.CTkLabel(
            self,
            text=f"Multiple {kind} Matches Found",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, padx=20, pady=(20, 2), sticky="w")
        ctk.CTkLabel(
            self,
            text=f"For file: {file_name}",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_LIGHT,
            wraplength=440,
            justify="left",
        ).grid(row=1, column=0, padx=20, pady=(0, 2), sticky="w")
        ctk.CTkLabel(
            self,
            text="This choice will be reused for the rest of this run.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            wraplength=440,
            justify="left",
        ).grid(row=2, column=0, padx=20, pady=(0, 10), sticky="w")

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=BG_CARD, corner_radius=8
        )
        self.list_frame.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)

        candidates = results[:8]
        for idx, r in enumerate(candidates):
            title = r.get("title") or r.get("name") or "Unknown Title"
            date = r.get("release_date") or r.get("first_air_date") or ""
            year = date[:4] if date else "—"
            overview = (r.get("overview") or "").strip()
            if len(overview) > 140:
                overview = overview[:137] + "..."

            row_frame = ctk.CTkFrame(
                self.list_frame, fg_color=BG_SIDEBAR, corner_radius=6
            )
            row_frame.grid(row=idx, column=0, sticky="ew", padx=5, pady=4)
            row_frame.grid_columnconfigure(1, weight=1)

            thumb = ctk.CTkLabel(
                row_frame,
                text="🎬",
                width=50,
                height=70,
                fg_color=BG_MAIN,
                corner_radius=4,
            )
            thumb.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
            self._thumb_labels[idx] = thumb

            ctk.CTkLabel(
                row_frame,
                text=f"{title} ({year})",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=TEXT_LIGHT,
                anchor="w",
                justify="left",
                wraplength=260,
            ).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(8, 0))
            ctk.CTkLabel(
                row_frame,
                text=overview or "No description available.",
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
                anchor="w",
                justify="left",
                wraplength=260,
            ).grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 4))

            ctk.CTkButton(
                row_frame,
                text="Use This",
                width=90,
                fg_color=TEAL_PRIMARY,
                hover_color=TEAL_HOVER,
                command=lambda r=r: self._pick(r),
            ).grid(row=0, column=2, rowspan=2, padx=8, pady=8)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, pady=(0, 20))
        ctk.CTkButton(
            btn_frame,
            text="Skip This File",
            width=140,
            fg_color=RED_CLEAR,
            hover_color=RED_CLEAR_HOVER,
            command=self._skip,
        ).grid(row=0, column=0, padx=10)
        if candidates:
            ctk.CTkButton(
                btn_frame,
                text="Use Top Result",
                width=140,
                fg_color="transparent",
                border_width=1,
                border_color=TEAL_PRIMARY,
                text_color=TEXT_LIGHT,
                command=lambda: self._pick(candidates[0]),
            ).grid(row=0, column=1, padx=10)

        if _HAVE_PIL and _HAVE_REQUESTS:
            threading.Thread(
                target=self._load_thumbnails, args=(candidates,), daemon=True
            ).start()

    def _load_thumbnails(self, candidates: list):
        for idx, r in enumerate(candidates):
            poster_path = r.get("poster_path")
            if not poster_path:
                continue
            try:
                img_bytes = self.tmdb_client.fetch_bytes(
                    self.tmdb_client.poster_url(poster_path, size="w92")
                )
                pil_img = Image.open(io.BytesIO(img_bytes))
                pil_img.load()
                self.after(0, self._apply_thumbnail, idx, pil_img)
            except Exception as e:
                print(
                    f"[MatchPickerDialog] Poster thumbnail fetch failed for '{r.get('title') or r.get('name')}': {e}"
                )
                continue

    def _apply_thumbnail(self, idx: int, pil_img):
        label = self._thumb_labels.get(idx)
        if not label:
            return
        try:
            ctk_img = ctk.CTkImage(
                light_image=pil_img, dark_image=pil_img, size=(50, 70)
            )
            label.configure(image=ctk_img, text="")
            label.image = ctk_img
        except Exception:
            pass

    def _pick(self, result: dict):
        self.choice_holder["match"] = result
        self._finish()

    def _skip(self):
        self.choice_holder["skip"] = True
        self._finish()

    def _on_close(self):
        self._finish()

    def _finish(self):
        self.event.set()
        self.destroy()


class APIKeyDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("TMDB API Setup")
        self.geometry("400x220")
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_MAIN)

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="TMDB API Key Required",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ORANGE_ACCENT,
        ).grid(row=0, column=0, pady=(20, 5))
        ctk.CTkLabel(
            self,
            text="Please enter your key to enable metadata fetching.",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_LIGHT,
        ).grid(row=1, column=0, pady=5)

        self.entry = ctk.CTkEntry(
            self,
            placeholder_text="Paste API Key here...",
            width=300,
            border_color=TEAL_PRIMARY,
        )
        self.entry.grid(row=2, column=0, pady=15)

        self.btn_save = ctk.CTkButton(
            self,
            text="Save & Continue",
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.save_key,
        )
        self.btn_save.grid(row=3, column=0, pady=(0, 20))

    def save_key(self):
        key = self.entry.get().strip()
        if len(key) < 10:
            messagebox.showerror("Invalid Key", "The API key provided seems too short.")
            return

        try:
            test_client = TMDBClient(api_key=key, timeout=5.0)
            test_client._request("/configuration", {})
        except Exception as e:
            messagebox.showerror(
                "Authentication Failed", f"TMDB rejected this API key.\n\nDetails: {e}"
            )
            return

        self.parent.tmdb_api_key = key
        os.environ["TMDB_API_KEY"] = key
        self.parent.save_config()
        messagebox.showinfo("Success", "API Key saved successfully!")
        self.destroy()


# ==============================================================================
# SECTION 8: MAIN APP UI & LOGIC
# ==============================================================================


class App(CustomDnDApp):
    def __init__(self):
        super().__init__()

        self.title("MediaForge")
        self.geometry("1200x850")
        self.minsize(1050, 700)

        self.tmdb_executor = ThreadPoolExecutor(max_workers=5)

        self.current_paths = []
        self.target_dirs = []
        self._current_poster_img = None
        self.file_statuses = {}
        self.tmdb_cache = {}
        self._update_job = None
        self._match_choice_cache = {}

        self.last_run_ops = self._load_undo_state() or {"renames": [], "created": []}
        self.config = {}
        self.tmdb_api_key = ""
        self.presets = DEFAULT_PRESETS.copy()

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config = json.load(f)
                    self.tmdb_api_key = self.config.get("TMDB_API_KEY", "")
                    if "presets" in self.config and isinstance(
                        self.config["presets"], dict
                    ):
                        self.presets = self.config["presets"]
            except Exception as e:
                print(f"Failed to read config file: {e}")

        self.save_config()

        if not self.tmdb_api_key:
            self.tmdb_api_key = os.environ.get("TMDB_API_KEY", "")
            if self.tmdb_api_key:
                os.environ["TMDB_API_KEY"] = self.tmdb_api_key
            else:
                self.after(1000, lambda: APIKeyDialog(self))

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self.on_file_drop)

        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(1, weight=2)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=0)

        self.sidebar = ctk.CTkFrame(
            self, width=220, corner_radius=0, fg_color=BG_SIDEBAR
        )
        self.sidebar.grid(row=0, column=0, rowspan=5, sticky="nsew")

        script_dir = Path(__file__).parent
        _ensure_assets(script_dir)

        icon_path = script_dir / "MediaForge.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        logo_path = script_dir / "MediaForge.png"
        try:
            logo_img = Image.open(logo_path)
            aspect_ratio = logo_img.width / logo_img.height
            self.ctk_logo = ctk.CTkImage(
                light_image=logo_img,
                dark_image=logo_img,
                size=(180, int(180 / aspect_ratio)),
            )
            self.logo_label = ctk.CTkLabel(self.sidebar, image=self.ctk_logo, text="")
            self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 30))

            try:
                icon_small = logo_img.copy()
                icon_small.thumbnail((64, 64))
                self._icon_photo = ImageTk.PhotoImage(icon_small)
                self.iconphoto(True, self._icon_photo)
            except Exception:
                pass
        except Exception:
            self.logo_label = ctk.CTkLabel(
                self.sidebar,
                text="MediaForge",
                font=ctk.CTkFont(size=24, weight="bold"),
                text_color=ORANGE_ACCENT,
            )
            self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 30))

        checkbox_kwargs = {
            "fg_color": ORANGE_ACCENT,
            "hover_color": ORANGE_HOVER,
            "text_color": TEXT_LIGHT,
            "command": self.on_config_change,
        }
        chk_labels = {
            "chk_poster": "Movie Poster",
            "chk_series_poster": "Series Poster",
            "chk_season_poster": "Season Posters",
            "chk_trailer": "Download Trailer",
            "chk_clean": "Clean Junk Files",
            "chk_prune": "Prune Folders",
            "chk_recursive": "Recursive Scan",
        }

        row = 1
        for group_title, chk_keys in CHK_GROUPS.items():
            ctk.CTkLabel(
                self.sidebar,
                text=group_title.upper(),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=TEXT_MUTED,
            ).grid(row=row, column=0, padx=20, pady=(14, 4), sticky="w")
            row += 1
            for key in chk_keys:
                cb = ctk.CTkCheckBox(
                    self.sidebar, text=chk_labels[key], **checkbox_kwargs
                )
                cb.grid(row=row, column=0, padx=20, pady=6, sticky="w")
                setattr(self, key, cb)
                row += 1

        self.chk_recursive.configure(command=self._rescan_if_needed)
        self.chk_recursive.select()

        has_undo_data = bool(
            self.last_run_ops.get("renames") or self.last_run_ops.get("created")
        )
        self.btn_undo = ctk.CTkButton(
            self.sidebar,
            text="↩️ Undo Last Run",
            fg_color="transparent",
            border_width=1,
            border_color=ORANGE_ACCENT,
            text_color=TEXT_LIGHT,
            hover_color=BG_CARD,
            command=self.undo_last_run,
            state=("normal" if has_undo_data else "disabled"),
        )
        self.btn_undo.grid(row=row + 1, column=0, padx=20, pady=(0, 10), sticky="s")

        self.btn_settings = ctk.CTkButton(
            self.sidebar,
            text="⚙️ Settings",
            fg_color="transparent",
            border_width=1,
            border_color=TEAL_PRIMARY,
            text_color=TEXT_LIGHT,
            hover_color=BG_CARD,
            command=self.open_settings,
        )
        self.btn_settings.grid(row=row + 3, column=0, padx=20, pady=(0, 20), sticky="s")
        self.sidebar.grid_rowconfigure(row + 2, weight=1)

        self.path_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.path_frame.grid(row=0, column=1, padx=(20, 10), pady=(20, 10), sticky="ew")
        self.path_frame.grid_columnconfigure(0, weight=1)
        self.path_entry = ctk.CTkEntry(
            self.path_frame,
            placeholder_text="Drag & drop file(s)/folder(s) here...",
            font=ctk.CTkFont(size=14),
            border_color=TEAL_PRIMARY,
            fg_color=BG_MAIN,
        )
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        self.btn_browse_folder = ctk.CTkButton(
            self.path_frame,
            text="📁 Folder",
            width=70,
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.browse_folder,
        )
        self.btn_browse_folder.grid(row=0, column=1, padx=(0, 10))
        self.btn_browse_file = ctk.CTkButton(
            self.path_frame,
            text="🎬 File",
            width=70,
            fg_color=TEAL_PRIMARY,
            hover_color=TEAL_HOVER,
            command=self.browse_file,
        )
        self.btn_browse_file.grid(row=0, column=2, padx=(0, 10))
        self.btn_clear = ctk.CTkButton(
            self.path_frame,
            text="🗑️ Clear",
            width=70,
            fg_color=RED_CLEAR,
            hover_color=RED_CLEAR_HOVER,
            text_color="white",
            command=self.clear_paths,
        )
        self.btn_clear.grid(row=0, column=3, padx=(0, 15))

        self.config_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.config_frame.grid(row=1, column=1, padx=(20, 10), pady=10, sticky="ew")
        self.config_frame.grid_columnconfigure((1, 4), weight=1)

        self.tmdb_type_var = ctk.StringVar(value="auto")

        ctk.CTkLabel(self.config_frame, text="Preset:", text_color=TEXT_LIGHT).grid(
            row=0, column=0, padx=15, pady=15, sticky="w"
        )
        self.preset_combo = ctk.CTkComboBox(
            self.config_frame,
            values=list(self.presets.keys()),
            width=200,
            border_color=TEAL_PRIMARY,
            button_color=TEAL_PRIMARY,
            button_hover_color=TEAL_HOVER,
            command=self.apply_preset,
        )
        self.preset_combo.grid(row=0, column=1, padx=10, pady=15, sticky="ew")

        ctk.CTkLabel(self.config_frame, text="Format:", text_color=TEXT_LIGHT).grid(
            row=0, column=2, padx=15, pady=15, sticky="w"
        )

        self.format_entry = ctk.CTkEntry(
            self.config_frame, width=280, border_color=TEAL_PRIMARY, fg_color=BG_MAIN
        )
        self.format_entry.bind("<KeyRelease>", self.on_config_change)
        self.format_entry.grid(row=0, column=3, padx=15, pady=15, sticky="ew")

        self.lbl_format_preview = ctk.CTkLabel(
            self.config_frame,
            text="",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
        )
        self.lbl_format_preview.grid(
            row=1, column=0, columnspan=4, padx=15, pady=(0, 12), sticky="ew"
        )

        self.queue_frame = ctk.CTkScrollableFrame(
            self, fg_color=BG_CARD, corner_radius=10
        )
        self.queue_frame.grid(row=2, column=1, padx=(20, 10), pady=10, sticky="nsew")

        self.log_box = ctk.CTkTextbox(
            self,
            height=100,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=BG_CARD,
            text_color=TEXT_LIGHT,
            border_color=BG_SIDEBAR,
            border_width=2,
        )
        self.log_box.grid(row=3, column=1, padx=(20, 10), pady=10, sticky="nsew")
        self.log_box.insert("0.0", "System Ready.\n")
        self.log_box.configure(state="disabled")

        self.action_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.action_frame.grid(
            row=4, column=1, padx=(20, 10), pady=(0, 20), sticky="ew"
        )
        self.action_frame.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(
            self.action_frame, progress_color=ORANGE_ACCENT
        )
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 20))
        self.progress.set(0)
        self.btn_dry_run = ctk.CTkButton(
            self.action_frame,
            text="Preview (Dry Run)",
            fg_color="transparent",
            border_width=2,
            border_color=TEAL_PRIMARY,
            text_color=TEXT_LIGHT,
            hover_color=BG_CARD,
            command=lambda: self.run_process(dry_run=True),
        )
        self.btn_dry_run.grid(row=0, column=1, padx=(0, 10))
        self.btn_run = ctk.CTkButton(
            self.action_frame,
            text="Forge Media",
            fg_color=ORANGE_ACCENT,
            hover_color=ORANGE_HOVER,
            text_color="white",
            command=lambda: self.run_process(dry_run=False),
        )
        self.btn_run.grid(row=0, column=2)

        self.preview_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.preview_frame.grid(
            row=0, column=2, rowspan=5, padx=(10, 20), pady=20, sticky="nsew"
        )
        self.preview_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.preview_frame,
            text="Preview",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_LIGHT,
        ).grid(row=0, column=0, pady=(15, 5))

        self.preview_stepper = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        self.preview_stepper.grid(row=1, column=0, pady=(0, 5))
        self.btn_preview_prev = ctk.CTkButton(
            self.preview_stepper,
            text="◀",
            width=28,
            fg_color="transparent",
            border_width=1,
            border_color=TEAL_PRIMARY,
            text_color=TEXT_LIGHT,
            hover_color=BG_MAIN,
            command=lambda: self._step_preview(-1),
        )
        self.btn_preview_prev.pack(side="left", padx=5)
        self.lbl_preview_index = ctk.CTkLabel(
            self.preview_stepper,
            text="",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.lbl_preview_index.pack(side="left", padx=5)
        self.btn_preview_next = ctk.CTkButton(
            self.preview_stepper,
            text="▶",
            width=28,
            fg_color="transparent",
            border_width=1,
            border_color=TEAL_PRIMARY,
            text_color=TEXT_LIGHT,
            hover_color=BG_MAIN,
            command=lambda: self._step_preview(1),
        )
        self.btn_preview_next.pack(side="left", padx=5)
        self.preview_stepper.grid_remove()

        self.preview_poster_label = ctk.CTkLabel(
            self.preview_frame,
            text="[ Poster ]",
            width=200,
            height=300,
            fg_color=BG_MAIN,
            corner_radius=8,
            text_color=TEXT_MUTED,
        )
        self.preview_poster_label.grid(row=2, column=0, padx=20, pady=10)

        self.lbl_preview_name = ctk.CTkLabel(
            self.preview_frame,
            text="Title (Year)",
            text_color=TEAL_PRIMARY,
            font=ctk.CTkFont(size=18, weight="bold"),
            wraplength=220,
            justify="center",
        )
        self.lbl_preview_name.grid(row=3, column=0, padx=20, pady=(10, 2))

        self.lbl_preview_ep_info = ctk.CTkLabel(
            self.preview_frame,
            text="",
            text_color=TEXT_LIGHT,
            font=ctk.CTkFont(size=13, weight="normal"),
            wraplength=220,
            justify="center",
        )
        self.lbl_preview_ep_info.grid(row=4, column=0, padx=20, pady=(0, 10))

        self.btn_trailer_preview = ctk.CTkButton(
            self.preview_frame,
            text="▶ Watch Trailer",
            fg_color="transparent",
            text_color=ORANGE_ACCENT,
            hover_color=BG_MAIN,
            border_width=1,
            border_color=ORANGE_ACCENT,
            cursor="hand2",
        )
        self.btn_trailer_preview.grid(row=5, column=0, padx=20, pady=(5, 10))

        self.lbl_orig_name = ctk.CTkLabel(
            self.preview_frame,
            text="File: -",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            wraplength=220,
            justify="center",
        )
        self.lbl_orig_name.grid(row=6, column=0, padx=20, pady=(15, 0))

        self.preview_frame.grid_remove()
        self.preview_index = 0

        self.preset_combo.set("")
        self.format_entry.delete(0, "end")
        self.update_queue_list()
        self.update_format_preview_label()

        def _shortcut_guard(handler):
            def wrapped(event):
                focused = self.focus_get()
                if focused is not None and focused.winfo_class() in ("Entry", "Text"):
                    return
                handler()

            return wrapped

        self.bind("<Return>", _shortcut_guard(lambda: self.run_process(dry_run=True)))
        self.bind(
            "<Control-Return>", _shortcut_guard(lambda: self.run_process(dry_run=False))
        )
        self.bind("<Control-z>", _shortcut_guard(self.undo_last_run))
        self.bind("<Control-Z>", _shortcut_guard(self.undo_last_run))

        saved_geometry = self.config.get("window_geometry")
        if saved_geometry:
            try:
                self.geometry(saved_geometry)
            except Exception:
                pass
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _on_app_close(self):
        try:
            self.tmdb_executor.shutdown(wait=False)
        except Exception:
            pass
        try:
            self.config["window_geometry"] = self.geometry()
            self.save_config()
        except Exception:
            pass
        self.destroy()

    def schedule_queue_update(self):
        if self._update_job is not None:
            self.after_cancel(self._update_job)
        self._update_job = self.after(250, self.update_queue_list)

    def save_config(self):
        self.config["TMDB_API_KEY"] = self.tmdb_api_key
        self.config["presets"] = self.presets
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Failed to write to config file: {e}")

    def open_settings(self):
        SettingsDialog(self)

    def _load_undo_state(self) -> Optional[dict]:
        if not UNDO_FILE.exists():
            return None
        try:
            with open(UNDO_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("renames") or data.get("created")):
                return data
        except Exception as e:
            print(f"Failed to read undo file: {e}")
        return None

    def _save_undo_state(self):
        try:
            with open(UNDO_FILE, "w") as f:
                json.dump(self.last_run_ops, f, indent=2)
        except Exception as e:
            print(f"Failed to write undo file: {e}")

    def _clear_undo_state(self):
        self.last_run_ops = {"renames": [], "created": []}
        try:
            if UNDO_FILE.exists():
                UNDO_FILE.unlink()
        except Exception as e:
            print(f"Failed to clear undo file: {e}")

    def undo_last_run(self):
        ops = self.last_run_ops
        n_renames = len(ops.get("renames", []))
        n_created = len(ops.get("created", []))
        if not (n_renames or n_created):
            messagebox.showinfo("Nothing to Undo", "There is no previous run to undo.")
            return

        if not messagebox.askyesno(
            "Undo Last Run",
            f"This will reverse {n_renames} rename(s) and delete {n_created} "
            f"downloaded poster/trailer file(s) from the last run.\n\n"
            f"Empty folders that were pruned will NOT be recreated. Continue?",
        ):
            return

        self.clear_log()
        self.btn_undo.configure(state="disabled")
        self.btn_dry_run.configure(state="disabled")
        self.btn_run.configure(state="disabled")
        threading.Thread(target=self._undo_thread, args=(ops,), daemon=True).start()

    def _undo_thread(self, ops: dict):
        self.log("Reversing last run...")
        errors = 0

        for f in ops.get("created", []):
            p = Path(f)
            try:
                if p.exists():
                    p.unlink()
                    self.log(f" -> Removed: {p.name}")
            except Exception as e:
                errors += 1
                self.log(f" -> [ERROR] Could not remove {p}: {e}")

        for old, new in reversed(ops.get("renames", [])):
            old_p, new_p = Path(old), Path(new)
            try:
                if not new_p.exists():
                    self.log(f" -> [SKIP] File no longer exists: {new_p}")
                    continue
                if old_p.exists():
                    self.log(f" -> [SKIP] Won't overwrite existing file: {old_p}")
                    errors += 1
                    continue
                old_p.parent.mkdir(parents=True, exist_ok=True)
                new_p.rename(old_p)
                self.log(f" -> Restored: {new_p.name} -> {old_p.name}")
            except Exception as e:
                errors += 1
                self.log(f" -> [ERROR] {e}")

        self.log(f"\nUndo complete. {errors} error(s).")
        self._clear_undo_state()
        self.after(0, self._undo_finished)

    def _undo_finished(self):
        self.btn_undo.configure(state="disabled")
        self.btn_dry_run.configure(state="normal")
        self.btn_run.configure(state="normal")

    def refresh_preset_ui(self, select_name=None):
        self.preset_combo.configure(values=list(self.presets.keys()))
        if select_name and select_name in self.presets:
            self.preset_combo.set(select_name)
            self.apply_preset(select_name)

    def _safe_clear_image(self, text=""):
        try:
            self.preview_poster_label.configure(image=None, text=text, fg_color=BG_MAIN)
        except Exception:
            try:
                self.preview_poster_label._label.configure(image="")
                self.preview_poster_label.configure(text=text, fg_color=BG_MAIN)
            except:
                pass
        self._current_poster_img = None

    def apply_preset(self, choice):
        for chk_name in CHK_MAPPING.keys():
            if hasattr(self, chk_name):
                getattr(self, chk_name).deselect()

        data = self.presets.get(choice, DEFAULT_PRESETS["Auto"])
        self.tmdb_type_var.set(data.get("type", "auto"))
        self.format_entry.delete(0, "end")
        self.format_entry.insert(0, data.get("format", ""))

        for chk_name in data.get("chk", []):
            if hasattr(self, chk_name):
                getattr(self, chk_name).select()

        self.update_queue_list()
        self.update_preview_panel()
        self.update_format_preview_label()

    def _compute_format_preview_text(self) -> str:
        if not self.current_paths:
            return "Add files above to preview the output name."

        fmt_string = self.format_entry.get()
        if not fmt_string.strip():
            return ""

        idx = max(0, min(self.preview_index, len(self.current_paths) - 1))
        path = self.current_paths[idx]
        try:
            is_tv = self._get_smart_is_tv(path)
            cache_entry = self.tmdb_cache.get(path)
            parsed = (
                cache_entry["parsed"] if cache_entry else parse_filename(path, is_tv)
            )

            ny = f"{parsed.title} ({parsed.year})" if parsed.year else parsed.title
            s_str = f"{int(parsed.season):02d}" if parsed.season is not None else "00"
            e_str = f"{int(parsed.episode):02d}" if parsed.episode is not None else "00"
            if parsed.episode2 is not None:
                e_str += f"-E{int(parsed.episode2):02d}"
            en = parsed.episode_title or f"Episode {e_str}"

            new_rel = fmt_string
            replacements = [
                (r"(?i)\{ny\}", ny),
                (r"(?i)\{t\}", parsed.title),
                (r"(?i)\{s00e00\}", f"S{s_str}E{e_str}"),
                (r"(?i)\{s\}", s_str),
                (r"(?i)\{e\}", e_str),
                (r"(?i)\{en\}", en),
            ]
            for pat, val in replacements:
                new_rel = re.sub(pat, lambda m, v=val: v, new_rel)

            clean = "".join(c for c in new_rel if c not in '<>:"\\|?*')
            return f"↳ {clean}{path.suffix.lower()}"
        except Exception:
            return "↳ (unable to preview this format for the current file)"

    def update_format_preview_label(self):
        try:
            self.lbl_format_preview.configure(text=self._compute_format_preview_text())
        except Exception:
            pass

    def remove_from_queue(self, path: Path):
        if path in self.current_paths:
            self.current_paths.remove(path)
        if path in self.file_statuses:
            del self.file_statuses[path]
        if path in self.tmdb_cache:
            del self.tmdb_cache[path]

        self.update_path_display()
        self.schedule_queue_update()
        self.update_preview_panel()
        self.update_format_preview_label()

    def on_file_drop(self, event):
        paths = self.tk.splitlist(event.data)
        if paths:
            self.add_paths(paths)

    def browse_folder(self):
        path = filedialog.askdirectory(title="Select Media Directory")
        if path:
            self.add_paths([path])

    def browse_file(self):
        paths = filedialog.askopenfilenames(
            title="Select Media Files",
            filetypes=[
                ("Video Files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv *.flv"),
                ("All Files", "*.*"),
            ],
        )
        if paths:
            self.add_paths(paths)

    def _rescan_if_needed(self):
        if self.target_dirs:
            self.current_paths.clear()
            self.add_paths([str(d) for d in self.target_dirs])

    def add_paths(self, paths):
        valid_exts = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv"}
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                if path_obj not in self.target_dirs:
                    self.target_dirs.append(path_obj)
                iterator = (
                    fast_rglob(path_obj)
                    if self.chk_recursive.get()
                    else path_obj.iterdir()
                )
                for sub_p in iterator:
                    if sub_p.is_file() and sub_p.suffix.lower() in valid_exts:
                        if sub_p not in self.current_paths:
                            self.current_paths.append(sub_p)
                            self.file_statuses[sub_p] = {}
            elif path_obj.is_file() and path_obj.suffix.lower() in valid_exts:
                if path_obj not in self.current_paths:
                    self.current_paths.append(path_obj)
                    self.file_statuses[path_obj] = {}

        self.update_path_display()
        self.schedule_queue_update()
        self.update_preview_panel()
        self.update_format_preview_label()

    def clear_paths(self):
        self.current_paths.clear()
        self.target_dirs.clear()
        self.file_statuses.clear()
        self.tmdb_cache.clear()
        self.preview_index = 0
        self.update_path_display()
        self.update_queue_list()
        self._safe_clear_image("[ Poster ]")
        self.update_preview_panel()
        self.update_format_preview_label()
        self.clear_log()
        self.log("Queue cleared.\n")

    def update_path_display(self):
        self.path_entry.delete(0, "end")
        count = len(self.current_paths)
        if count == 1:
            self.path_entry.insert(0, str(self.current_paths[0]))
        elif count > 1:
            self.path_entry.insert(0, f"[{count} items added to queue]")

    def on_config_change(self, *args):
        self.update_queue_list()
        self.update_preview_panel()
        self.update_format_preview_label()

    def _get_smart_is_tv(self, path: Path) -> bool:
        if self.tmdb_type_var.get() == "tv":
            return True
        if self.tmdb_type_var.get() == "movie":
            return False
        parsed = parse_filename(path, True)
        return parsed.season is not None

    def _resolve_tmdb_match(
        self, results: list, parsed, is_tv: bool, file_name: str, client: "TMDBClient"
    ):
        if not results:
            return None, False

        confident = confident_tmdb_match(results, parsed.title, parsed.year)
        if confident:
            return confident, False

        if len(results) == 1:
            return results[0], False

        cache_key = (is_tv, parsed.title.strip().lower(), parsed.year)
        if cache_key in self._match_choice_cache:
            return self._match_choice_cache[cache_key], False

        choice_holder: dict = {}
        event = threading.Event()
        self.after(
            0,
            lambda: MatchPickerDialog(
                self, results, is_tv, file_name, choice_holder, event, client
            ),
        )
        event.wait()

        if choice_holder.get("skip"):
            return None, True

        chosen = choice_holder.get("match") or results[0]
        self._match_choice_cache[cache_key] = chosen
        return chosen, False

    def _fetch_tmdb_for_queue(self, path: Path, parsed, is_tv: bool):
        key = self.tmdb_api_key
        if not key:
            self.tmdb_cache[path]["status"] = "error"
            self.after(0, self.schedule_queue_update)
            return

        client = TMDBClient(api_key=key)
        try:
            results = (
                client.search_tv_all(parsed.title, parsed.year)
                if is_tv
                else client.search_movie_all(parsed.title, parsed.year)
            )
            if results:
                match = (
                    confident_tmdb_match(results, parsed.title, parsed.year)
                    or results[0]
                )
                parsed.title = match.get("title") or match.get("name", parsed.title)
                rel_date = match.get("release_date") or match.get("first_air_date", "")
                if rel_date:
                    parsed.year = rel_date[:4]

                if is_tv and parsed.season is not None and parsed.episode is not None:
                    try:
                        s_data = client.season_details(match["id"], parsed.season)
                        for ep in s_data.get("episodes", []):
                            if ep.get("episode_number") == parsed.episode:
                                parsed.episode_title = "".join(
                                    c
                                    for c in ep.get("name", "")
                                    if c not in '<>:"\\|?*'
                                )
                                break
                    except:
                        pass

            self.tmdb_cache[path]["status"] = "done"
        except Exception:
            self.tmdb_cache[path]["status"] = "error"

        self.after(0, self.schedule_queue_update)

    def _run_status_icon(self, path: Path) -> str:
        statuses = self.file_statuses.get(path)
        if not statuses:
            return ""
        values = list(statuses.values())
        if "error" in values:
            return STATUS_ICONS["error"]
        if "pending" in values:
            return STATUS_ICONS["pending"]
        if "done" in values:
            return STATUS_ICONS["done"]
        return ""

    def update_queue_list(self):
        for widget in self.queue_frame.winfo_children():
            widget.destroy()

        if not self.current_paths:
            empty_frame = ctk.CTkFrame(self.queue_frame, fg_color="transparent")
            empty_frame.pack(expand=True, fill="both", pady=60)
            ctk.CTkLabel(empty_frame, text="📁", font=ctk.CTkFont(size=32)).pack()
            ctk.CTkLabel(
                empty_frame,
                text="Queue is empty",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=TEXT_MUTED,
            ).pack(pady=(6, 2))
            ctk.CTkLabel(
                empty_frame,
                text="Drag & drop files or folders above, or use\nthe Folder / File buttons to get started.",
                font=ctk.CTkFont(size=11),
                text_color=TEXT_MUTED,
                justify="center",
            ).pack()
            return

        fmt_string = self.format_entry.get()
        display_paths = self.current_paths[:100]

        grouped_paths = {}

        for path in display_paths:
            try:
                is_tv = self._get_smart_is_tv(path)

                if path not in self.tmdb_cache:
                    offline_parsed = parse_filename(path, is_tv)
                    self.tmdb_cache[path] = {
                        "status": "loading",
                        "parsed": offline_parsed,
                    }
                    self.tmdb_executor.submit(
                        self._fetch_tmdb_for_queue, path, offline_parsed, is_tv
                    )

                cache_entry = self.tmdb_cache[path]
                parsed = cache_entry["parsed"]
                status = cache_entry["status"]

                title_display = (
                    parsed.title
                    if status != "loading"
                    else f"{parsed.title} (Fetching...)"
                )
                ny = (
                    f"{title_display} ({parsed.year})" if parsed.year else title_display
                )
                s_str = (
                    f"{int(parsed.season):02d}" if parsed.season is not None else "00"
                )

                e_str = (
                    f"{int(parsed.episode):02d}" if parsed.episode is not None else "00"
                )
                if parsed.episode2 is not None:
                    e_str += f"-E{int(parsed.episode2):02d}"

                en = parsed.episode_title or f"Episode {e_str}"

                new_rel_str = fmt_string
                replacements = [
                    (r"(?i)\{ny\}", ny),
                    (r"(?i)\{t\}", title_display),
                    (r"(?i)\{s00e00\}", f"S{s_str}E{e_str}"),
                    (r"(?i)\{s\}", s_str),
                    (r"(?i)\{e\}", e_str),
                    (r"(?i)\{en\}", en),
                ]

                for pat, val in replacements:
                    new_rel_str = re.sub(pat, lambda m, v=val: v, new_rel_str)

                clean_str = "".join(c for c in new_rel_str if c not in '<>:"\\|?*')

                if "/" in clean_str:
                    parts = clean_str.split("/")
                    dir_key = ("folder", "/".join(parts[:-1]))
                    file_part = parts[-1]
                elif is_tv:
                    series_label = (
                        f"{parsed.title} ({parsed.year})"
                        if parsed.year
                        else parsed.title
                    )
                    dir_key = ("series", series_label)
                    file_part = clean_str
                else:
                    dir_key = ("flat", "Root Directory (Flat)")
                    file_part = clean_str

            except Exception as exc:
                dir_key = ("error", "Error")
                file_part = f"(Error: {exc})"

            if dir_key not in grouped_paths:
                grouped_paths[dir_key] = []
            grouped_paths[dir_key].append((path, file_part, status))

        def create_hover_bindings(item_widget, btn_widget, child_widgets):
            def on_hover_change(e):
                try:
                    x, y = item_widget.winfo_pointerx(), item_widget.winfo_pointery()
                    x1, y1 = item_widget.winfo_rootx(), item_widget.winfo_rooty()
                    x2, y2 = (
                        x1 + item_widget.winfo_width(),
                        y1 + item_widget.winfo_height(),
                    )
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        btn_widget.configure(text_color=TEXT_LIGHT)
                    else:
                        btn_widget.configure(text_color=TEXT_MUTED)
                except Exception:
                    pass

            item_widget.bind("<Enter>", on_hover_change)
            item_widget.bind("<Leave>", on_hover_change)
            for w in child_widgets:
                w.bind("<Enter>", on_hover_change)
                w.bind("<Leave>", on_hover_change)

        group_icons = {"folder": "📁", "series": "📺", "flat": "📌", "error": "⚠️"}
        for (kind, dir_name), items in grouped_paths.items():
            card = ctk.CTkFrame(self.queue_frame, fg_color=BG_SIDEBAR, corner_radius=6)
            card.pack(fill="x", padx=5, pady=3)

            head_frame = ctk.CTkFrame(card, fg_color="transparent", height=24)
            head_frame.pack(fill="x", padx=8, pady=(4, 2))

            folder_icon = group_icons.get(kind, "📁")
            header_text = f"{folder_icon} {dir_name}"
            if kind == "series" and len(items) > 1:
                header_text += f"  ·  {len(items)} episodes"
            ctk.CTkLabel(
                head_frame,
                text=header_text,
                text_color=TEAL_PRIMARY,
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(side="left")

            for path, file_part, status in items:
                item_frame = ctk.CTkFrame(card, fg_color="transparent")
                item_frame.pack(fill="x", padx=8, pady=(1, 1))

                status_icon = self._run_status_icon(path)
                name_text = (
                    f"{status_icon} 📄 {path.name}"
                    if status_icon
                    else f"📄 {path.name}"
                )
                ctk.CTkLabel(
                    item_frame,
                    text=name_text,
                    text_color=TEXT_MUTED,
                    font=ctk.CTkFont(size=11),
                    width=250,
                    anchor="w",
                ).pack(side="left")

                btn_del = ctk.CTkButton(
                    item_frame,
                    text="✖",
                    width=20,
                    height=20,
                    fg_color="transparent",
                    text_color=TEXT_MUTED,
                    hover_color=RED_CLEAR,
                    command=lambda p=path: self.remove_from_queue(p),
                )
                btn_del.pack(side="right")

                name_tb = ctk.CTkTextbox(
                    item_frame,
                    height=20,
                    fg_color="transparent",
                    text_color=TEXT_LIGHT,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    wrap="none",
                    activate_scrollbars=False,
                )
                name_tb.pack(fill="x", side="left", expand=True, padx=(5, 0))

                prefix = "↳ "
                name_tb.insert("0.0", f"{prefix}{file_part}")

                name_tb.tag_config("season", foreground=ORANGE_ACCENT)
                for match in re.finditer(r"S\d{2,}E\d{2,}(?:-E\d{2,})?", file_part):
                    start_idx = match.start() + len(prefix)
                    end_idx = match.end() + len(prefix)
                    name_tb.tag_add("season", f"1.{start_idx}", f"1.{end_idx}")

                name_tb.configure(state="disabled")
                create_hover_bindings(
                    item_frame, btn_del, [item_frame, name_tb, btn_del]
                )

    def _step_preview(self, delta: int):
        if not self.current_paths:
            return
        self.preview_index = (self.preview_index + delta) % len(self.current_paths)
        self.update_preview_panel()
        self.update_format_preview_label()

    def update_preview_panel(self):
        if not self.current_paths:
            self.preview_frame.grid_remove()
            return

        total = len(self.current_paths)
        self.preview_index = max(0, min(self.preview_index, total - 1))
        path = self.current_paths[self.preview_index]
        if not path.is_file():
            return

        self.preview_frame.grid()
        if total > 1:
            self.preview_stepper.grid()
            self.lbl_preview_index.configure(text=f"{self.preview_index + 1} / {total}")
        else:
            self.preview_stepper.grid_remove()

        self.lbl_orig_name.configure(text=f"Original: {path.name}")
        self.lbl_preview_ep_info.configure(text="")

        try:
            is_tv = self._get_smart_is_tv(path)
            parsed = parse_filename(path, is_tv)
            self.lbl_preview_name.configure(text=f"{parsed.title} (Searching...)")
            self._safe_clear_image("[ Searching TMDB... ]")
            self.btn_trailer_preview.configure(state="disabled", text="▶ Loading...")
            threading.Thread(
                target=self._fetch_tmdb_data, args=(parsed, is_tv), daemon=True
            ).start()
        except Exception:
            self.lbl_preview_name.configure(text=path.stem)
            self._safe_clear_image("[ Parse Error ]")

    def _fetch_tmdb_data(self, parsed, is_tv):
        key = self.tmdb_api_key
        if not key:
            self.after(0, self._apply_tmdb_error, "API Key Missing", parsed, is_tv)
            return

        client = TMDBClient(api_key=key)
        try:
            results = (
                client.search_tv_all(parsed.title, parsed.year)
                if is_tv
                else client.search_movie_all(parsed.title, parsed.year)
            )
            if not results:
                self.after(0, self._apply_tmdb_error, "No TMDB Match", parsed, is_tv)
                return

            best_match = (
                confident_tmdb_match(results, parsed.title, parsed.year) or results[0]
            )
            official_title = best_match.get("title") or best_match.get(
                "name", parsed.title
            )
            release_date = best_match.get("release_date") or best_match.get(
                "first_air_date", ""
            )
            official_year = release_date[:4] if release_date else parsed.year

            ep_full_info = ""
            if is_tv and parsed.season is not None and parsed.episode is not None:
                try:
                    season_data = client.season_details(best_match["id"], parsed.season)
                    for ep in season_data.get("episodes", []):
                        if ep.get("episode_number") == parsed.episode:
                            ep_name = ep.get("name", f"Episode {parsed.episode}")
                            ep_full_info = (
                                f"S{parsed.season:02d}E{parsed.episode:02d} - {ep_name}"
                            )
                            break
                except:
                    ep_full_info = f"S{parsed.season:02d}E{parsed.episode:02d}"

            pil_img = None
            poster_path = best_match.get("poster_path")
            if poster_path and _HAVE_PIL:
                img_bytes = client.fetch_bytes(
                    client.poster_url(poster_path, size="w342")
                )
                pil_img = Image.open(io.BytesIO(img_bytes))

            self.after(
                0,
                self._apply_tmdb_success,
                official_title,
                official_year,
                ep_full_info,
                pil_img,
            )
        except Exception:
            self.after(0, self._apply_tmdb_error, "Network Error", parsed, is_tv)

    def _apply_tmdb_success(self, title, year, ep_info, pil_img):
        self._safe_clear_image("")
        display_text = f"{title}" + (f" ({year})" if year else "")
        self.lbl_preview_name.configure(text=display_text)
        self.lbl_preview_ep_info.configure(text=ep_info)

        if pil_img:
            disp_width = 200
            disp_height = int(disp_width / (pil_img.width / pil_img.height))
            self._current_poster_img = ctk.CTkImage(
                light_image=pil_img, dark_image=pil_img, size=(disp_width, disp_height)
            )
            try:
                self.preview_poster_label.configure(
                    image=self._current_poster_img, fg_color="transparent"
                )
            except:
                pass
        else:
            self._current_poster_img = None
            self.preview_poster_label.configure(
                text="[ No Poster Found ]", fg_color=BG_MAIN
            )

        search_query = display_text.replace(" ", "+")
        self.btn_trailer_preview.configure(
            state="normal",
            text="▶ Watch Trailer",
            command=lambda: webbrowser.open(
                f"https://www.youtube.com/results?search_query={search_query}+official+trailer"
            ),
        )

    def _apply_tmdb_error(self, error_msg, parsed, is_tv):
        self._safe_clear_image(f"[ {error_msg} ]")
        try:
            self.preview_poster_label.configure(fg_color=BG_MAIN)
        except:
            pass

        display_text = f"{parsed.title}" + (f" ({parsed.year})" if parsed.year else "")
        self.lbl_preview_name.configure(text=display_text)
        ep_info = ""
        if is_tv and parsed.season is not None:
            ep_info = f"S{parsed.season:02d}E{parsed.episode:02d}" + (
                f" - {parsed.episode_title}" if parsed.episode_title else ""
            )
        self.lbl_preview_ep_info.configure(text=ep_info)

        search_query = display_text.replace(" ", "+")
        self.btn_trailer_preview.configure(
            state="normal",
            text="▶ Search Trailer",
            command=lambda: webbrowser.open(
                f"https://www.youtube.com/results?search_query={search_query}+trailer"
            ),
        )

    def log(self, message: str):
        self.after(0, self._log_safe, message)

    def _log_safe(self, message: str):
        self.log_box.configure(state="normal")

        lower = message.lower()
        if "[error]" in lower or "[tmdb poster error]" in lower:
            tag = "log_error"
            color = RED_CLEAR
        elif (
            "[skip]" in lower
            or "[prune warn]" in lower
            or "no poster found" in lower
            or "skipped tmdb match" in lower
        ):
            tag = "log_warn"
            color = ORANGE_ACCENT
        elif (
            "complete" in lower
            or "-> renamed" in lower
            or "-> restored" in lower
            or "done." in lower
        ):
            tag = "log_ok"
            color = GREEN_SUCCESS
        else:
            tag = None

        start_index = self.log_box.index("end-1c")
        self.log_box.insert("end", message + "\n")
        if tag:
            self.log_box.tag_config(tag, foreground=color)
            self.log_box.tag_add(tag, start_index, self.log_box.index("end-1c"))

        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    def run_process(self, dry_run: bool):
        if not self.current_paths:
            return
        self.clear_log()
        self.btn_dry_run.configure(state="disabled")
        self.btn_run.configure(state="disabled")
        self.btn_undo.configure(state="disabled")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        threading.Thread(
            target=self._process_thread, args=(dry_run,), daemon=True
        ).start()

    def _process_thread(self, dry_run: bool):
        fmt_string = self.format_entry.get()
        if not dry_run:
            self.pending_run_ops = {"renames": [], "created": []}
        self._match_choice_cache = {}
        if self.chk_clean.get():
            for d in self.target_dirs:
                if d.exists() and d.is_dir():
                    self.log(f"Cleaning junk in: {d.name}...")
                    clean_artifacts(d, dry_run)

        client = TMDBClient(api_key=self.tmdb_api_key) if self.tmdb_api_key else None
        total_files = len(self.current_paths)

        for i, file_path in enumerate(self.current_paths):
            if file_path not in self.file_statuses:
                self.file_statuses[file_path] = {}

            self.log(f"[{i + 1}/{total_files}] Processing: {file_path.name}")
            self.after(0, self.progress.set, (i + 1) / total_files)
            try:
                is_tv = self._get_smart_is_tv(file_path)
                parsed = parse_filename(file_path, is_tv)

                resolved_match = None
                if client:
                    results = (
                        client.search_tv_all(parsed.title, parsed.year)
                        if is_tv
                        else client.search_movie_all(parsed.title, parsed.year)
                    )
                    match, skipped = self._resolve_tmdb_match(
                        results, parsed, is_tv, file_path.name, client
                    )
                    if skipped:
                        self.log(
                            " -> Skipped TMDB match for this file (using parsed name only)."
                        )
                    elif match:
                        resolved_match = match
                        parsed.title = match.get("title") or match.get(
                            "name", parsed.title
                        )
                        rel_date = match.get("release_date") or match.get(
                            "first_air_date", ""
                        )
                        if rel_date:
                            parsed.year = rel_date[:4]

                        if (
                            is_tv
                            and parsed.season is not None
                            and parsed.episode is not None
                        ):
                            try:
                                s_data = client.season_details(
                                    match["id"], parsed.season
                                )
                                for ep in s_data.get("episodes", []):
                                    if ep.get("episode_number") == parsed.episode:
                                        parsed.episode_title = "".join(
                                            c
                                            for c in ep.get("name", "")
                                            if c not in '<>:"\\|?*'
                                        )
                                        break
                            except:
                                pass

                ny = f"{parsed.title} ({parsed.year})" if parsed.year else parsed.title
                clean_ny = "".join(c for c in ny if c not in '<>:"\\|?*')
                s_str = (
                    f"{int(parsed.season):02d}" if parsed.season is not None else "00"
                )

                e_str = (
                    f"{int(parsed.episode):02d}" if parsed.episode is not None else "00"
                )
                if parsed.episode2 is not None:
                    e_str += f"-E{int(parsed.episode2):02d}"

                en = parsed.episode_title or f"Episode {e_str}"

                new_rel = fmt_string
                replacements = [
                    (r"(?i)\{ny\}", ny),
                    (r"(?i)\{t\}", parsed.title),
                    (r"(?i)\{s00e00\}", f"S{s_str}E{e_str}"),
                    (r"(?i)\{s\}", s_str),
                    (r"(?i)\{e\}", e_str),
                    (r"(?i)\{en\}", en),
                ]

                for pat, val in replacements:
                    new_rel = re.sub(pat, lambda m, v=val: v, new_rel)

                clean_p = "".join(c for c in new_rel if c not in '<>:"\\|?*')

                parts = clean_p.split("/")
                if parts:
                    parts[-1] += file_path.suffix.lower()

                base_d = self.target_dirs[0] if self.target_dirs else file_path.parent
                dest_path = base_d / Path(*parts)

                self.log(f" -> Renaming to: {dest_path.name}")
                req_post = (
                    self.chk_poster.get()
                    if not is_tv
                    else (self.chk_series_poster.get() or self.chk_season_poster.get())
                )
                if not dry_run:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    original_dest = dest_path
                    counter = 1
                    while dest_path.exists() and dest_path != file_path:
                        dest_path = original_dest.with_name(
                            f"{original_dest.stem} ({counter}){original_dest.suffix}"
                        )
                        counter += 1

                    if file_path != dest_path:
                        file_path.rename(dest_path)
                        self.file_statuses[file_path]["rename"] = "done"
                        self.pending_run_ops["renames"].append(
                            (str(file_path), str(dest_path))
                        )
                    else:
                        self.log(f" -> [SKIP] File is already correctly named.")

                if req_post and not dry_run:
                    self.log(" -> Fetching poster from TMDB...")
                    try:
                        tmdb = TMDBClient(self.tmdb_api_key)
                        if resolved_match:
                            results = [resolved_match]
                        else:
                            results = (
                                tmdb.search_tv_all(parsed.title, parsed.year)
                                if is_tv
                                else tmdb.search_movie_all(parsed.title, parsed.year)
                            )

                        if results and results[0].get("poster_path"):
                            series_root = dest_path.parent
                            if is_tv and len(parts) > 1:
                                best_idx = 0
                                for idx, p in enumerate(parts[:-1]):
                                    if parsed.title.lower() in p.lower():
                                        best_idx = idx
                                        break
                                series_root = base_d / Path(*parts[: best_idx + 1])

                            if (not is_tv and self.chk_poster.get()) or (
                                is_tv and self.chk_series_poster.get()
                            ):
                                img_url = tmdb.poster_url(
                                    results[0]["poster_path"], "w780"
                                )
                                poster_data = tmdb.fetch_bytes(img_url)
                                poster_name = (
                                    f"{clean_ny} - Series Poster.jpg"
                                    if is_tv
                                    else f"{clean_ny} - Poster.jpg"
                                )
                                poster_dest = series_root / poster_name

                                if not poster_dest.exists():
                                    poster_dest.parent.mkdir(
                                        parents=True, exist_ok=True
                                    )
                                    poster_dest.write_bytes(poster_data)
                                    self.pending_run_ops["created"].append(
                                        str(poster_dest)
                                    )

                            if (
                                is_tv
                                and self.chk_season_poster.get()
                                and parsed.season is not None
                            ):
                                tv_id = results[0]["id"]
                                season_data = tmdb.season_details(tv_id, parsed.season)
                                if season_data and season_data.get("poster_path"):
                                    s_url = tmdb.poster_url(
                                        season_data["poster_path"], "w780"
                                    )
                                    s_data = tmdb.fetch_bytes(s_url)
                                    s_dest = (
                                        dest_path.parent
                                        / f"Season {parsed.season:02d} Poster.jpg"
                                    )

                                    if not s_dest.exists():
                                        s_dest.parent.mkdir(parents=True, exist_ok=True)
                                        s_dest.write_bytes(s_data)
                                        self.pending_run_ops["created"].append(
                                            str(s_dest)
                                        )

                            self.file_statuses[file_path]["poster"] = "done"
                        else:
                            self.file_statuses[file_path]["poster"] = "error"
                            self.log(" -> No poster found on TMDB.")

                    except Exception as e:
                        self.file_statuses[file_path]["poster"] = "error"
                        self.log(f" -> [TMDB Poster Error] {e}")

                elif self.chk_poster.get() and dry_run:
                    self.file_statuses[file_path]["poster"] = "pending"
                if self.chk_trailer.get() and not dry_run:
                    trailer_dest = dest_path.parent / f"{clean_ny} - Trailer"
                    yt_url = f"ytsearch1:{parsed.title} {parsed.year} official trailer"
                    self.log(f" -> Downloading trailer...")
                    success, trailer_result_path = download_trailer(
                        yt_url, trailer_dest
                    )
                    self.file_statuses[file_path]["trailer"] = (
                        "done" if success else "error"
                    )
                    if trailer_result_path.exists():
                        self.pending_run_ops["created"].append(str(trailer_result_path))

                self.after(0, self.schedule_queue_update)

            except Exception as e:
                self.log(f" -> [ERROR] {e}")
                self.file_statuses[file_path]["rename"] = "error"
                self.after(0, self.schedule_queue_update)

        self.log("\nForging complete.")
        if self.chk_prune.get() and not dry_run:
            self.log("Pruning empty folders...")
            pruned = 0
            dirs_to_check = sorted(
                {f.parent for f in self.current_paths} | set(self.target_dirs),
                key=lambda p: len(p.parts),
                reverse=True,
            )
            for d in dirs_to_check:
                try:
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                        pruned += 1
                        self.log(f" -> Removed empty folder: {d.name}")
                except Exception as e:
                    self.log(f" -> [PRUNE WARN] {d}: {e}")
            if pruned == 0:
                self.log(" -> No empty folders found.")

        if not dry_run:
            self.last_run_ops = self.pending_run_ops
            self._save_undo_state()

        self.after(0, self._process_finished, dry_run)

    def _process_finished(self, dry_run: bool = False):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1)
        self.btn_dry_run.configure(state="normal")
        self.btn_run.configure(state="normal")
        has_undo_data = bool(
            self.last_run_ops.get("renames") or self.last_run_ops.get("created")
        )
        self.btn_undo.configure(state=("normal" if has_undo_data else "disabled"))

        if getattr(self, "auto_quit", False):
            self.destroy()


# ==============================================================================
# SECTION 9: ENTRYPOINT
# ==============================================================================


def run_cli(argv: None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv

    import argparse

    parser = argparse.ArgumentParser(description="MediaForge")
    parser.add_argument("paths", nargs="*", help="Files or folders to load")
    parser.add_argument("--preset", help="Name of the preset to select")
    parser.add_argument(
        "--auto-run", action="store_true", help="Run in background and exit"
    )
    args = parser.parse_args(args_list)

    app = App()

    if args.auto_run:
        app.withdraw()
        app.auto_quit = True

    if args.paths:
        app.add_paths(args.paths)

    if args.preset and args.preset in app.presets:
        app.preset_combo.set(args.preset)
        app.apply_preset(args.preset)

    if args.auto_run:
        app.run_process(dry_run=False)

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
