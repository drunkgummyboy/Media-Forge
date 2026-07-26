# ==============================================================================
# SECTION 1: IMPORTS & SETUP
# ==============================================================================
from __future__ import annotations

import sys
import subprocess

# --- AUTO-INSTALLER ---
def _auto_install_deps():
    reqs = {
        "customtkinter": "customtkinter",
        "tkinterdnd2": "tkinterdnd2",
        "pillow": "PIL",
        "requests": "requests",
        "yt-dlp": "yt_dlp",
        "imageio-ffmpeg": "imageio_ffmpeg"
    }
    
    to_install = []
    for pip_name, import_name in reqs.items():
        try:
            __import__(import_name)
        except ImportError:
            to_install.append(pip_name)
    
    # STEP 1: Install any completely missing dependencies first
    if to_install:
        print(f"[*] Missing dependencies found. Auto-installing: {', '.join(to_install)}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *to_install])
            print("[*] Dependencies installed successfully!")
            import site
            import importlib
            importlib.reload(site) 
        except Exception as e:
            print(f"[!] Failed to auto-install dependencies: {e}")
            print("[!] Please install them manually using: pip install " + " ".join(to_install))
            sys.exit(1)

    # STEP 2: Silently check for and install updates for all required packages
    print("[*] Checking for package updates (this may take a few seconds)...")
    try:
        # The '--upgrade' flag tells pip to update the packages if a newer version exists
        # The '--quiet' flag hides the massive wall of text pip normally generates
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", *reqs.keys()],
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        # If the update fails (e.g., no internet connection), we just ignore it so the app still launches
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
    import imageio_ffmpeg # type: ignore
    _HAVE_FFMPEG_BIN = True
except Exception:
    _HAVE_FFMPEG_BIN = False

# --- REBRANDING & GLOBALS ---
# --- REBRANDING & GLOBALS ---
APP_TITLE = "MediaForge"
CONFIG_FILE = Path(__file__).parent / "mediaforge_config.json"
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv"}

FORBIDDEN = set('<>:"/\\|?*') | {chr(c) for c in range(0, 32)}
SPACE_RE = re.compile(r"\s+")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)")

# Safely match a second episode number if separated by combinations of -, &, +, 'and', or 'E'
# Negative lookahead prevents false positives with resolutions/codecs like '1080p', '720i' or 'x264'
EP2_SUFFIX_STD = r"(?:(?:[.\s]*(?:-|&|\+|and|E|Ep|Episode))+[.\s]*(?!(?:720|1080|264|265|480|2160)(?!\d))(\d{1,3})(?!\d|[pPiI]))?"
EP2_SUFFIX_X = r"(?:(?:[.\s]*(?:-|&|\+|and|x|X))+[.\s]*(?!(?:720|1080|264|265|480|2160)(?!\d))(\d{1,3})(?!\d|[pPiI]))?"

# Highly forgiving series regex updated for multi-episode (e.g., S01E01-02, S01E01-E02, or S01E01 & 2)
SERIES_RE = re.compile(r"(?i)(?<![a-z])(?:S|Season)[.\s]*(\d{1,4})[.\s-]*(?:E|Ep|Episode)[.\s]*(\d{1,4})" + EP2_SUFFIX_STD + r"(?![a-z])")
SERIES_X_RE = re.compile(r"(?i)(?<!\d)(\d{1,4})[.\s]*[xX][.\s]*(\d{1,4})" + EP2_SUFFIX_X + r"(?!\d)")
EP_ONLY_RE = re.compile(r"(?i)(?<![a-z])(?:Ep(?:isode)?|E)[.\s]*(\d{1,3})" + EP2_SUFFIX_STD + r"(?![a-z])")


GROUP_SUFFIX_RE = re.compile(r"\s*-\s*[A-Za-z0-9]+$")
BRACKETS_RE = re.compile(r"[\[\(]([^\]\)]{0,128})[\]\)]")

TAG_PATTERNS = {
    "resolution": re.compile(r"(?i)\b(?:480p|720p|1080p|2160p|4k|8k)\b"),
    "source": re.compile(r"(?i)\b(?:bluray|b[dr]rip|web[-.]?dl|webrip|web|hdrip|hdtv|dvdrip)\b"),
    "codec": re.compile(r"(?i)\b(?:x264|x265|h\.?264|h\.?265|hevc|av1)\b"),
    "audio": re.compile(r"(?i)\b(?:dts\.?hd|dts|aac|ac3|truehd|atmos)\b"),
    "edition": re.compile(r"(?i)\b(?:director'?s cut|extended|unrated|remastered|imax|criterion)\b"),
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
    if not token: return token
    if token.isupper(): return token
    return token[:1].upper() + token[1:].lower()

def human_title(s: str) -> str:
    parts = [capitalize_human(p) for p in s.split()]
    return " ".join(parts) or "_"

def _extract_series_bits(text: str) -> Tuple[Optional[int], Optional[int], Optional[int], str, str, str]:
    m = SERIES_RE.search(text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        e2 = int(m.group(3)) if m.group(3) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return s, e, e2, se, text[:m.start()].strip(" -_."), text[m.end():].strip(" -_.")
    
    m = SERIES_X_RE.search(text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        e2 = int(m.group(3)) if m.group(3) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return s, e, e2, se, text[:m.start()].strip(" -_."), text[m.end():].strip(" -_.")
        
    m = EP_ONLY_RE.search(text)
    if m:
        s, e = 1, int(m.group(1))
        e2 = int(m.group(2)) if m.group(2) else None
        se = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
        return s, e, e2, se, text[:m.start()].strip(" -_."), text[m.end():].strip(" -_.")
        
    return None, None, None, "", text.strip(" -_."), ""

def parse_filename(path: Path, is_series: bool) -> ParsedName:
    stem = path.stem
    bracket_bits = [m.group(1) for m in BRACKETS_RE.finditer(stem)]
    stem = BRACKETS_RE.sub(" ", stem)

    s = collapse_spaces(stem.replace(".", " ").replace("_", " "))
    
    season, episode, episode2, se_tag = None, None, None, ""
    ep_title_raw = ""
    
    # Extract the Season and Episode FIRST before the suffix cleaner strips it away
    ss, ee, ee2, se, left_part, right_part = _extract_series_bits(s)
    if ss is not None and ee is not None:
        season, episode, episode2, se_tag = ss, ee, ee2, se
        s = collapse_spaces(left_part)
        ep_title_raw = right_part

    # Now we can safely clean trailing release group names from the title base
    while True:
        new_s = GROUP_SUFFIX_RE.sub("", s)
        if new_s == s: break
        s = collapse_spaces(new_s)
        
    # And clean trailing release group names from the episode title
    while True:
        new_ep = GROUP_SUFFIX_RE.sub("", ep_title_raw)
        if new_ep == ep_title_raw: break
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
            right = s[m.end():].strip(" -_.")
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

    # ADDED dd5\.?1 and dd5 to the audio tag parsing so it strips properly
    updated_tag_patterns = TAG_PATTERNS.copy()
    updated_tag_patterns["audio"] = re.compile(r"(?i)\b(?:dts\.?hd|dts|aac|ac3|truehd|atmos|dd5\.?1|dd5)\b")

    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(right): add_extra(collapse_spaces(hit))

    left_clean = left
    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(left_clean): add_extra(collapse_spaces(hit))
        left_clean = rgx.sub(" ", left_clean)
    left_clean = collapse_spaces(left_clean)
    
    ep_title_clean = ep_title_raw
    for rgx in updated_tag_patterns.values():
        for hit in rgx.findall(ep_title_clean): add_extra(collapse_spaces(hit))
        ep_title_clean = rgx.sub(" ", ep_title_clean)
    ep_title_clean = collapse_spaces(ep_title_clean)

    title_raw = left_clean or left or "_"
    for b in bracket_bits: add_extra(collapse_spaces(b))
    extra_line = " • ".join([se_tag] + extras_set).strip(" •")

    return ParsedName(
        title=human_title(title_raw) or "_", 
        year=year, 
        se_tag=se_tag, 
        extra=extra_line, 
        season=season, 
        episode=episode, 
        episode2=episode2,
        episode_title=human_title(ep_title_clean)
    )
# ==============================================================================
# SECTION 3: TMDB CLIENT 
# ==============================================================================
class TMDBClient:
    BASE = "https://api.themoviedb.org/3"
    IMG_BASE = "https://image.tmdb.org/t/p"

    def __init__(self, api_key: str, language: Optional[str] = None, timeout: float = 10.0):
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
            data = self._request("/search/movie", {k: v for k, v in params.items() if v})
            results.extend(data.get("results") or [])
            
        seen, uniq = set(), []
        for r in results:
            if r.get("id") not in seen:
                uniq.append(r); seen.add(r.get("id"))
        return uniq

    def search_tv_all(self, query: str, year: Optional[str] = None) -> List[dict]:
        params = {"query": query}
        if year: params["first_air_date_year"] = year
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
        
        # Inject the portable FFmpeg binary location right into yt-dlp
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
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best", 
            "--merge-output-format", "mp4", 
            "--extractor-args", "youtube:client=tv,mweb,ios",
            "-o", outtmpl
        ]
        
        if _HAVE_FFMPEG_BIN:
            import imageio_ffmpeg
            cmd.extend(["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()])
            
        cmd.append(url)
            
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return True
    except Exception as e:
        print(f"[yt-dlp binary error] {e}")
        return False

def download_trailer(url: str, dest_noext: Path) -> Tuple[bool, Path]:
    _ensure_dir(dest_noext.parent)
    outtmpl = str(dest_noext) + ".%(ext)s"
    if _download_trailer_with_module(url, outtmpl) or _download_trailer_with_binary(url, outtmpl):
        for ext in (".mp4", ".mkv", ".webm", ".m4v"):
            # Safely append extension as string to prevent Pathlib dot truncation
            cand = Path(str(dest_noext) + ext)
            if cand.exists():
                return True, cand
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
        if (dirpath / f"{base_stem}{ext}").exists(): return True
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
    """Recursively checks if a directory contains any video files."""
    for f in fast_rglob(root):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            return True
    return False

def clean_artifacts(root: Path, dry_run: bool) -> tuple[int, int, int]:
    junk = orphan_posters = url_cleaned = 0
    if not root.exists(): return (0, 0, 0)
    
    for p in fast_rglob(root):
        name = p.name
        lower_suffix = p.suffix.lower()
        try:
            if name in JUNK_FILENAMES:
                if not dry_run: p.unlink(missing_ok=True)
                junk += 1
                continue
                
            if lower_suffix == ".url":
                base = p.stem
                if _has_matching_video(p.parent, base):
                    if not dry_run: p.unlink(missing_ok=True)
                    url_cleaned += 1
                continue
                
            if lower_suffix == ".jpg":
                # 1. Check Standard Movie Poster
                m = POSTER_RE.match(p.name)
                if m and not _has_matching_video(p.parent, m.group("base")):
                    if not dry_run: p.unlink(missing_ok=True)
                    orphan_posters += 1
                    continue
                    
                # 2. Check Series Poster (Valid as long as ANY video exists in sub-folders)
                m_series = SERIES_POSTER_RE.match(p.name)
                if m_series:
                    if not _dir_has_videos(p.parent):
                        if not dry_run: p.unlink(missing_ok=True)
                        orphan_posters += 1
                    continue

                # 3. Check Season Poster (Valid if any video exists in the exact season folder)
                if SEASON_POSTER_RE.match(p.name):
                    if not any(True for _ in (f for f in p.parent.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS)):
                        if not dry_run: p.unlink(missing_ok=True)
                        orphan_posters += 1
        except Exception as ex:
            print(f"[CLEAN WARN] {p}: {ex}")
            
    print(f"Clean summary: junk={junk}, orphans={orphan_posters}, trailer_links={url_cleaned}")
    return junk, orphan_posters, url_cleaned

# ==============================================================================
# SECTION 6: GUI GLOBALS & SETUP
# ==============================================================================

# --- BRAND COLOR PALETTE ---
BG_MAIN = "#223038"         
BG_SIDEBAR = "#1A262B"      
BG_CARD = "#2B3C45"         
TEAL_PRIMARY = "#3A7D90"    
TEAL_HOVER = "#2F6676"
ORANGE_ACCENT = "#E86A33"   
ORANGE_HOVER = "#CF5A27"
RED_CLEAR = "#D9534F"
RED_CLEAR_HOVER = "#C9302C"
TEXT_LIGHT = "#E0E6ED"
GREEN_SUCCESS = "#4CAF50"

ctk.set_appearance_mode("Dark")

DEFAULT_PRESETS = {
    "Auto": {"type": "auto", "format": "{ny}", "chk": []},
    "Movie (Folder)": {"type": "movie", "format": "{ny}/{ny}", "chk": ["chk_poster", "chk_trailer"]},
    "Movie (Flat)": {"type": "movie", "format": "{ny}", "chk": []},
    "Series (Folder)": {"type": "tv", "format": "{ny}/{ny} - Season {s}/{ny} - {s00e00} - {en}", "chk": ["chk_series_poster", "chk_season_poster"]},
    "Series (Flat)": {"type": "tv", "format": "{ny} - {s00e00} - {en}", "chk": []},
}

CHK_MAPPING = {
    "chk_poster": "Movie Poster",
    "chk_series_poster": "Series Poster",
    "chk_season_poster": "Season Posters",
    "chk_trailer": "Download Trailer",
    "chk_clean": "Clean Junk Files",
    "chk_prune": "Prune Folders",
    "chk_recursive": "Recursive Scan"
}

class CustomDnDApp(ctk.CTk, TkinterDnD.DnDWrapper):
    """Wrapper to enable Drag and Drop capabilities on CustomTkinter."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)
        
        
# ==============================================================================
# SECTION 7: DIALOG WINDOWS
# ==============================================================================

class SettingsDialog(ctk.CTkToplevel):
    """Window to manage Settings, TMDB API Key, and Presets."""
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Settings & Presets")
        self.geometry("480x750")
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(0, weight=1)
        
        self.preset_type = ctk.StringVar(value="auto")
        self.chk_vars = {k: ctk.BooleanVar(value=False) for k in CHK_MAPPING.keys()}

        # --- API KEY SECTION ---
        api_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        api_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        api_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(api_frame, text="TMDB API Key", font=ctk.CTkFont(size=14, weight="bold"), text_color=ORANGE_ACCENT).grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")
        
        api_inner = ctk.CTkFrame(api_frame, fg_color="transparent")
        api_inner.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        api_inner.grid_columnconfigure(0, weight=1)
        
        self.api_entry = ctk.CTkEntry(api_inner, placeholder_text="Enter TMDB API Key...", border_color=TEAL_PRIMARY)
        self.api_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.api_entry.insert(0, self.parent.tmdb_api_key)
        
        ctk.CTkButton(api_inner, text="Save Key", width=80, fg_color=TEAL_PRIMARY, hover_color=TEAL_HOVER, command=self.save_api_key).grid(row=0, column=1)

        # --- PRESET MANAGEMENT SECTION ---
        ctk.CTkLabel(self, text="Manage Presets", font=ctk.CTkFont(size=16, weight="bold"), text_color=TEAL_PRIMARY).grid(row=1, column=0, pady=(15, 5))
        
        self.preset_selector = ctk.CTkOptionMenu(self, values=list(self.parent.presets.keys()), width=350, fg_color=BG_CARD, button_color=TEAL_PRIMARY, button_hover_color=TEAL_HOVER, command=self.load_preset_to_ui)
        self.preset_selector.grid(row=2, column=0, pady=10)

        form_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        form_frame.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        form_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form_frame, text="Preset Name:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.name_entry = ctk.CTkEntry(form_frame, border_color=TEAL_PRIMARY)
        self.name_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(form_frame, text="Media Type:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.type_seg = ctk.CTkSegmentedButton(form_frame, values=["auto", "movie", "tv"], variable=self.preset_type, selected_color=TEAL_PRIMARY, selected_hover_color=TEAL_HOVER)
        self.type_seg.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(form_frame, text="Format:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.format_entry = ctk.CTkEntry(form_frame, border_color=TEAL_PRIMARY)
        self.format_entry.grid(row=2, column=1, padx=10, pady=10, sticky="ew")

        mods_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        mods_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(mods_frame, text="Active Modifiers:", text_color=ORANGE_ACCENT).pack(anchor="w", pady=(0, 5))
        
        chk_container = ctk.CTkFrame(mods_frame, fg_color="transparent")
        chk_container.pack(fill="x")
        chk_container.grid_columnconfigure(0, weight=1)
        chk_container.grid_columnconfigure(1, weight=1)
        
        row_idx, col_idx = 0, 0
        for k, label_text in CHK_MAPPING.items():
            cb = ctk.CTkCheckBox(chk_container, text=label_text, variable=self.chk_vars[k], fg_color=ORANGE_ACCENT, hover_color=ORANGE_HOVER)
            cb.grid(row=row_idx, column=col_idx, sticky="w", pady=5)
            col_idx += 1
            if col_idx > 1:
                col_idx = 0
                row_idx += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, pady=20)
        
        ctk.CTkButton(btn_frame, text="💾 Save / Overwrite", width=140, fg_color=TEAL_PRIMARY, hover_color=TEAL_HOVER, command=self.save_preset).grid(row=0, column=0, padx=10)
        ctk.CTkButton(btn_frame, text="🗑️ Delete Preset", width=140, fg_color=RED_CLEAR, hover_color=RED_CLEAR_HOVER, command=self.delete_preset).grid(row=0, column=1, padx=10)

        if self.parent.presets:
            first_key = list(self.parent.presets.keys())[0]
            self.preset_selector.set(first_key)
            self.load_preset_to_ui(first_key)

    def save_api_key(self):
        key = self.api_entry.get().strip()
        self.parent.tmdb_api_key = key
        os.environ["TMDB_API_KEY"] = key
        self.parent.save_config()
        messagebox.showinfo("Saved", "API Key has been saved successfully.")

    def load_preset_to_ui(self, name):
        data = self.parent.presets.get(name)
        if not data: return
        self.name_entry.delete(0, 'end')
        self.name_entry.insert(0, name)
        self.format_entry.delete(0, 'end')
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
            "chk": active_chks
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
            messagebox.showerror("Error", "You cannot delete the last remaining preset.")
            return
            
        del self.parent.presets[name]
        self.parent.save_config()
        
        new_default = list(self.parent.presets.keys())[0]
        self.parent.refresh_preset_ui(new_default)
        self.preset_selector.configure(values=list(self.parent.presets.keys()))
        self.preset_selector.set(new_default)
        self.load_preset_to_ui(new_default)


class APIKeyDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("TMDB API Setup")
        self.geometry("400x220")
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_MAIN)
        
        self.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(self, text="TMDB API Key Required", font=ctk.CTkFont(size=16, weight="bold"), text_color=ORANGE_ACCENT).grid(row=0, column=0, pady=(20, 5))
        ctk.CTkLabel(self, text="Please enter your key to enable metadata fetching.", font=ctk.CTkFont(size=12), text_color=TEXT_LIGHT).grid(row=1, column=0, pady=5)
        
        self.entry = ctk.CTkEntry(self, placeholder_text="Paste API Key here...", width=300, border_color=TEAL_PRIMARY)
        self.entry.grid(row=2, column=0, pady=15)
        
        self.btn_save = ctk.CTkButton(self, text="Save & Continue", fg_color=TEAL_PRIMARY, hover_color=TEAL_HOVER, command=self.save_key)
        self.btn_save.grid(row=3, column=0, pady=(0, 20))

    def save_key(self):
        key = self.entry.get().strip()
        if len(key) < 10:
            messagebox.showerror("Invalid Key", "The API key provided seems too short.")
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

        # Internal state
        self.current_paths = []
        self.target_dirs = []
        self._current_poster_img = None
        self.file_statuses = {}
        self.tmdb_cache = {}  # Background cache for queue display
        self._update_job = None  # Debounce timer variable

        # --- Configuration & Presets Load ---
        self.config = {}
        self.tmdb_api_key = ""
        self.presets = DEFAULT_PRESETS.copy()

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config = json.load(f)
                    self.tmdb_api_key = self.config.get("TMDB_API_KEY", "")
                    if "presets" in self.config and isinstance(self.config["presets"], dict):
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
        self.dnd_bind('<<Drop>>', self.on_file_drop)

        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(1, weight=2)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(2, weight=1) 
        self.grid_rowconfigure(3, weight=0) 

        # --- Sidebar ---
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=BG_SIDEBAR)
        self.sidebar.grid(row=0, column=0, rowspan=5, sticky="nsew")
        self.sidebar.grid_rowconfigure(10, weight=1)

        try:
            script_dir = Path(__file__).parent
            icon_path = script_dir / "MediaForge.ico"
            self.iconbitmap(str(icon_path))
            
            logo_path = script_dir / "MediaForge.png"
            logo_img = Image.open(logo_path)
            aspect_ratio = logo_img.width / logo_img.height
            self.ctk_logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(180, int(180 / aspect_ratio)))
            self.logo_label = ctk.CTkLabel(self.sidebar, image=self.ctk_logo, text="")
            self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 30))
        except Exception:
            self.logo_label = ctk.CTkLabel(self.sidebar, text="MediaForge", font=ctk.CTkFont(size=24, weight="bold"), text_color=ORANGE_ACCENT)
            self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 30))

        checkbox_kwargs = {"fg_color": ORANGE_ACCENT, "hover_color": ORANGE_HOVER, "text_color": TEXT_LIGHT, "command": self.on_config_change}
        
        self.chk_poster = ctk.CTkCheckBox(self.sidebar, text="Movie Poster", **checkbox_kwargs)
        self.chk_poster.grid(row=1, column=0, padx=20, pady=10, sticky="w")
        
        self.chk_series_poster = ctk.CTkCheckBox(self.sidebar, text="Series Poster", **checkbox_kwargs)
        self.chk_series_poster.grid(row=2, column=0, padx=20, pady=10, sticky="w")
        
        self.chk_season_poster = ctk.CTkCheckBox(self.sidebar, text="Season Posters", **checkbox_kwargs)
        self.chk_season_poster.grid(row=3, column=0, padx=20, pady=10, sticky="w")
        
        self.chk_trailer = ctk.CTkCheckBox(self.sidebar, text="Download Trailer", **checkbox_kwargs)
        self.chk_trailer.grid(row=4, column=0, padx=20, pady=10, sticky="w")

        self.chk_clean = ctk.CTkCheckBox(self.sidebar, text="Clean Junk Files", **checkbox_kwargs)
        self.chk_clean.grid(row=5, column=0, padx=20, pady=10, sticky="w")

        self.chk_prune = ctk.CTkCheckBox(self.sidebar, text="Prune Folders", **checkbox_kwargs)
        self.chk_prune.grid(row=6, column=0, padx=20, pady=10, sticky="w")

        self.chk_recursive = ctk.CTkCheckBox(self.sidebar, text="Recursive Scan", **checkbox_kwargs)
        self.chk_recursive.grid(row=7, column=0, padx=20, pady=10, sticky="w")
        self.chk_recursive.configure(command=self._rescan_if_needed)
        self.chk_recursive.select() 

        self.btn_settings = ctk.CTkButton(self.sidebar, text="⚙️ Settings", fg_color="transparent", border_width=1, border_color=TEAL_PRIMARY, text_color=TEXT_LIGHT, hover_color=BG_CARD, command=self.open_settings)
        self.btn_settings.grid(row=11, column=0, padx=20, pady=(0, 20), sticky="s")

        # --- Main Content Area ---
        self.path_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.path_frame.grid(row=0, column=1, padx=(20, 10), pady=(20, 10), sticky="ew")
        self.path_frame.grid_columnconfigure(0, weight=1)
        self.path_entry = ctk.CTkEntry(self.path_frame, placeholder_text="Drag & drop file(s)/folder(s) here...", font=ctk.CTkFont(size=14), border_color=TEAL_PRIMARY, fg_color=BG_MAIN)
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        self.btn_browse_folder = ctk.CTkButton(self.path_frame, text="📁 Folder", width=70, fg_color=TEAL_PRIMARY, hover_color=TEAL_HOVER, command=self.browse_folder)
        self.btn_browse_folder.grid(row=0, column=1, padx=(0, 10))
        self.btn_browse_file = ctk.CTkButton(self.path_frame, text="🎬 File", width=70, fg_color=TEAL_PRIMARY, hover_color=TEAL_HOVER, command=self.browse_file)
        self.btn_browse_file.grid(row=0, column=2, padx=(0, 10))
        self.btn_clear = ctk.CTkButton(self.path_frame, text="🗑️ Clear", width=70, fg_color=RED_CLEAR, hover_color=RED_CLEAR_HOVER, text_color="white", command=self.clear_paths)
        self.btn_clear.grid(row=0, column=3, padx=(0, 15))

        # --- Configuration & Presets ---
        self.config_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.config_frame.grid(row=1, column=1, padx=(20, 10), pady=10, sticky="ew")
        self.config_frame.grid_columnconfigure((1, 4), weight=1)
        
        self.tmdb_type_var = ctk.StringVar(value="auto")

        ctk.CTkLabel(self.config_frame, text="Preset:", text_color=TEXT_LIGHT).grid(row=0, column=0, padx=15, pady=15, sticky="w")
        self.preset_combo = ctk.CTkComboBox(self.config_frame, values=list(self.presets.keys()), width=200, border_color=TEAL_PRIMARY, button_color=TEAL_PRIMARY, button_hover_color=TEAL_HOVER, command=self.apply_preset)
        self.preset_combo.grid(row=0, column=1, padx=10, pady=15, sticky="ew")

        ctk.CTkLabel(self.config_frame, text="Format:", text_color=TEXT_LIGHT).grid(row=0, column=2, padx=15, pady=15, sticky="w")
        
        self.format_entry = ctk.CTkEntry(self.config_frame, width=280, border_color=TEAL_PRIMARY, fg_color=BG_MAIN)
        self.format_entry.bind("<KeyRelease>", self.on_config_change)
        self.format_entry.grid(row=0, column=3, padx=15, pady=15, sticky="ew")

        # QUEUE LIST
        self.queue_frame = ctk.CTkScrollableFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.queue_frame.grid(row=2, column=1, padx=(20, 10), pady=10, sticky="nsew")

        # LOG BOX
        self.log_box = ctk.CTkTextbox(self, height=100, font=ctk.CTkFont(family="Consolas", size=12), fg_color=BG_CARD, text_color=TEXT_LIGHT, border_color=BG_SIDEBAR, border_width=2)
        self.log_box.grid(row=3, column=1, padx=(20, 10), pady=10, sticky="nsew")
        self.log_box.insert("0.0", "System Ready.\n")
        self.log_box.configure(state="disabled")

        # ACTION BAR
        self.action_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.action_frame.grid(row=4, column=1, padx=(20, 10), pady=(0, 20), sticky="ew")
        self.action_frame.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(self.action_frame, progress_color=ORANGE_ACCENT)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 20))
        self.progress.set(0)
        self.btn_dry_run = ctk.CTkButton(self.action_frame, text="Preview (Dry Run)", fg_color="transparent", border_width=2, border_color=TEAL_PRIMARY, text_color=TEXT_LIGHT, hover_color=BG_CARD, command=lambda: self.run_process(dry_run=True))
        self.btn_dry_run.grid(row=0, column=1, padx=(0, 10))
        self.btn_run = ctk.CTkButton(self.action_frame, text="Forge Media", fg_color=ORANGE_ACCENT, hover_color=ORANGE_HOVER, text_color="white", command=lambda: self.run_process(dry_run=False))
        self.btn_run.grid(row=0, column=2)

        # --- Right Panel (Preview Column) ---
        self.preview_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        self.preview_frame.grid(row=0, column=2, rowspan=5, padx=(10, 20), pady=20, sticky="nsew")
        self.preview_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.preview_frame, text="Preview", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_LIGHT).grid(row=0, column=0, pady=(15, 5))
        self.preview_poster_label = ctk.CTkLabel(self.preview_frame, text="[ Poster ]", width=200, height=300, fg_color=BG_MAIN, corner_radius=8, text_color="gray")
        self.preview_poster_label.grid(row=1, column=0, padx=20, pady=10)

        self.lbl_preview_name = ctk.CTkLabel(self.preview_frame, text="Title (Year)", text_color=TEAL_PRIMARY, font=ctk.CTkFont(size=18, weight="bold"), wraplength=220, justify="center")
        self.lbl_preview_name.grid(row=2, column=0, padx=20, pady=(10, 2))
        
        self.lbl_preview_ep_info = ctk.CTkLabel(self.preview_frame, text="", text_color=TEXT_LIGHT, font=ctk.CTkFont(size=13, weight="normal"), wraplength=220, justify="center")
        self.lbl_preview_ep_info.grid(row=3, column=0, padx=20, pady=(0, 10))

        self.btn_trailer_preview = ctk.CTkButton(self.preview_frame, text="▶ Watch Trailer", fg_color="transparent", text_color=ORANGE_ACCENT, hover_color=BG_MAIN, border_width=1, border_color=ORANGE_ACCENT, cursor="hand2")
        self.btn_trailer_preview.grid(row=4, column=0, padx=20, pady=(5, 10))

        self.lbl_orig_name = ctk.CTkLabel(self.preview_frame, text="File: -", text_color="gray", font=ctk.CTkFont(size=11), wraplength=220, justify="center")
        self.lbl_orig_name.grid(row=5, column=0, padx=20, pady=(15, 0))

        self.preview_frame.grid_remove()

        # Load empty slate by default
        self.preset_combo.set("")
        self.format_entry.delete(0, 'end')
        self.update_queue_list()

    def schedule_queue_update(self):
        """Debounces queue redraws to prevent UI freezing during mass updates."""
        if self._update_job is not None:
            self.after_cancel(self._update_job)
        # Wait 250ms before redrawing. If another call comes in, the timer resets.
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
            except: pass
        self._current_poster_img = None

    def apply_preset(self, choice):
        for chk_name in CHK_MAPPING.keys():
            if hasattr(self, chk_name): getattr(self, chk_name).deselect()
            
        data = self.presets.get(choice, DEFAULT_PRESETS["Auto"])
        
        self.tmdb_type_var.set(data.get("type", "auto"))
        
        self.format_entry.delete(0, 'end')
        self.format_entry.insert(0, data.get("format", ""))
        
        for chk_name in data.get("chk", []):
            if hasattr(self, chk_name): getattr(self, chk_name).select()
            
        self.update_queue_list()
        self.update_preview_panel()

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

    def on_file_drop(self, event):
        paths = self.tk.splitlist(event.data)
        if paths: self.add_paths(paths)

    def browse_folder(self):
        path = filedialog.askdirectory(title="Select Media Directory")
        if path: self.add_paths([path])

    def browse_file(self):
        paths = filedialog.askopenfilenames(title="Select Media Files", filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv *.flv"), ("All Files", "*.*")])
        if paths: self.add_paths(paths)
            
    def _rescan_if_needed(self):
        if self.target_dirs:
            self.current_paths.clear()
            self.add_paths([str(d) for d in self.target_dirs])

    def add_paths(self, paths):
        valid_exts = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv"}
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                if path_obj not in self.target_dirs: self.target_dirs.append(path_obj)
                iterator = fast_rglob(path_obj) if self.chk_recursive.get() else path_obj.iterdir()
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

    def clear_paths(self):
        self.current_paths.clear()
        self.target_dirs.clear()
        self.file_statuses.clear()
        self.tmdb_cache.clear()
        self.update_path_display()
        self.update_queue_list()
        self._safe_clear_image("[ Poster ]")
        self.update_preview_panel()
        self.clear_log()
        self.log("Queue cleared.\n")

    def update_path_display(self):
        self.path_entry.delete(0, "end")
        count = len(self.current_paths)
        if count == 1: self.path_entry.insert(0, str(self.current_paths[0]))
        elif count > 1: self.path_entry.insert(0, f"[{count} items added to queue]")

    def on_config_change(self, *args):
        self.update_queue_list()
        self.update_preview_panel()

    def _get_smart_is_tv(self, path: Path) -> bool:
        if self.tmdb_type_var.get() == "tv": return True
        if self.tmdb_type_var.get() == "movie": return False
        parsed = parse_filename(path, True)
        return parsed.season is not None

    def _fetch_tmdb_for_queue(self, path: Path, parsed, is_tv: bool):
        key = self.tmdb_api_key
        if not key:
            self.tmdb_cache[path]["status"] = "error"
            self.after(0, self.schedule_queue_update)
            return

        client = TMDBClient(api_key=key)
        try:
            results = client.search_tv_all(parsed.title, parsed.year) if is_tv else client.search_movie_all(parsed.title, parsed.year)
            if results:
                match = results[0]
                parsed.title = match.get("title") or match.get("name", parsed.title)
                rel_date = match.get("release_date") or match.get("first_air_date", "")
                if rel_date: parsed.year = rel_date[:4]
                
                if is_tv and parsed.season is not None and parsed.episode is not None:
                    try:
                        s_data = client.season_details(match["id"], parsed.season)
                        for ep in s_data.get("episodes", []):
                            if ep.get("episode_number") == parsed.episode:
                                parsed.episode_title = "".join(c for c in ep.get("name", "") if c not in '<>:"\\|?*')
                                break
                    except: pass
            
            self.tmdb_cache[path]["status"] = "done"
        except Exception:
            self.tmdb_cache[path]["status"] = "error"
            
        self.after(0, self.schedule_queue_update)

    def update_queue_list(self):
        for widget in self.queue_frame.winfo_children(): widget.destroy()

        if not self.current_paths:
            ctk.CTkLabel(self.queue_frame, text="Queue is empty.", text_color="gray").pack(pady=20)
            return

        fmt_string = self.format_entry.get()
        display_paths = self.current_paths[:100]

        # --- PASS 1: Calculate names and group them by target directory ---
        grouped_paths = {}

        for path in display_paths:
            try:
                is_tv = self._get_smart_is_tv(path)
                
                if path not in self.tmdb_cache:
                    offline_parsed = parse_filename(path, is_tv)
                    self.tmdb_cache[path] = {"status": "loading", "parsed": offline_parsed}
                    threading.Thread(target=self._fetch_tmdb_for_queue, args=(path, offline_parsed, is_tv), daemon=True).start()
                
                cache_entry = self.tmdb_cache[path]
                parsed = cache_entry["parsed"]
                status = cache_entry["status"]

                title_display = parsed.title if status != "loading" else f"{parsed.title} (Fetching...)"
                ny = f"{title_display} ({parsed.year})" if parsed.year else title_display
                s_str = f"{int(parsed.season):02d}" if parsed.season is not None else "00"
                
                e_str = f"{int(parsed.episode):02d}" if parsed.episode is not None else "00"
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
                    (r"(?i)\{en\}", en)
                ]
                
                for pat, val in replacements:
                    new_rel_str = re.sub(pat, lambda m, v=val: v, new_rel_str)
                
                clean_str = "".join(c for c in new_rel_str if c not in '<>:"\\|?*')
                
                # Split path to group by folder
                if "/" in clean_str:
                    parts = clean_str.split("/")
                    dir_part = "/".join(parts[:-1])
                    file_part = parts[-1]
                else:
                    dir_part = "Root Directory (Flat)"
                    file_part = clean_str
                    
            except Exception as exc:
                dir_part = "Error"
                file_part = f"(Error: {exc})"

            if dir_part not in grouped_paths:
                grouped_paths[dir_part] = []
            grouped_paths[dir_part].append((path, file_part, status))

        # Helper to bind hover events per-item so the 'X' button only shows for the row you hover
        def create_hover_bindings(item_widget, btn_widget, child_widgets):
            def on_hover_change(e):
                try:
                    x, y = item_widget.winfo_pointerx(), item_widget.winfo_pointery()
                    x1, y1 = item_widget.winfo_rootx(), item_widget.winfo_rooty()
                    x2, y2 = x1 + item_widget.winfo_width(), y1 + item_widget.winfo_height()
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        btn_widget.configure(text_color=TEXT_LIGHT)
                    else:
                        btn_widget.configure(text_color=BG_SIDEBAR) 
                except Exception:
                    pass
            
            item_widget.bind("<Enter>", on_hover_change)
            item_widget.bind("<Leave>", on_hover_change)
            for w in child_widgets:
                w.bind("<Enter>", on_hover_change)
                w.bind("<Leave>", on_hover_change)

        # --- PASS 2: Render the grouped UI ---
        for dir_name, items in grouped_paths.items():
            # Card Container for the whole folder
            card = ctk.CTkFrame(self.queue_frame, fg_color=BG_SIDEBAR, corner_radius=6)
            card.pack(fill="x", padx=5, pady=3)
            
            # Card Header (Folder Name)
            head_frame = ctk.CTkFrame(card, fg_color="transparent", height=24)
            head_frame.pack(fill="x", padx=8, pady=(4, 2))
            
            folder_icon = "📁" if dir_name not in ["Root Directory (Flat)", "Error"] else "📌"
            ctk.CTkLabel(head_frame, text=f"{folder_icon} {dir_name}", text_color=TEAL_PRIMARY, font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

            # Render individual files inside this folder
            for path, file_part, status in items:
                item_frame = ctk.CTkFrame(card, fg_color="transparent")
                item_frame.pack(fill="x", padx=8, pady=(1, 1))
                
                # Left side: Original file name
                ctk.CTkLabel(item_frame, text=f"📄 {path.name}", text_color="gray", font=ctk.CTkFont(size=11), width=250, anchor="w").pack(side="left")
                
                # Right side: Delete button
                btn_del = ctk.CTkButton(item_frame, text="✖", width=20, height=20, fg_color="transparent", text_color=BG_SIDEBAR, hover_color=RED_CLEAR, command=lambda p=path: self.remove_from_queue(p))
                btn_del.pack(side="right")
                
                # Middle: Projected new file name (Drastically reduced height)
                name_tb = ctk.CTkTextbox(item_frame, height=20, fg_color="transparent", text_color=TEXT_LIGHT, font=ctk.CTkFont(size=12, weight="bold"), wrap="none", activate_scrollbars=False)
                name_tb.pack(fill="x", side="left", expand=True, padx=(5, 0))
                
                prefix = "↳ "
                name_tb.insert("0.0", f"{prefix}{file_part}")
                
                # Keep season highlighting
                name_tb.tag_config("season", foreground=ORANGE_ACCENT)
                for match in re.finditer(r"S\d{2,}E\d{2,}(?:-E\d{2,})?", file_part):
                    start_idx = match.start() + len(prefix)
                    end_idx = match.end() + len(prefix)
                    name_tb.tag_add("season", f"1.{start_idx}", f"1.{end_idx}")
                    
                name_tb.configure(state="disabled")

                # Bind hover to this specific row
                create_hover_bindings(item_frame, btn_del, [item_frame, name_tb, btn_del])
        
    def update_preview_panel(self):
        if not self.current_paths or len(self.current_paths) != 1:
            self.preview_frame.grid_remove()
            return

        path = self.current_paths[0]
        if not path.is_file(): return
        
        self.preview_frame.grid()
        self.lbl_orig_name.configure(text=f"Original: {path.name}")
        self.lbl_preview_ep_info.configure(text="")
        
        try:
            is_tv = self._get_smart_is_tv(path)
            parsed = parse_filename(path, is_tv)
            self.lbl_preview_name.configure(text=f"{parsed.title} (Searching...)")
            self._safe_clear_image("[ Searching TMDB... ]")
            self.btn_trailer_preview.configure(state="disabled", text="▶ Loading...")
            threading.Thread(target=self._fetch_tmdb_data, args=(parsed, is_tv), daemon=True).start()
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
            results = client.search_tv_all(parsed.title, parsed.year) if is_tv else client.search_movie_all(parsed.title, parsed.year)
            if not results:
                self.after(0, self._apply_tmdb_error, "No TMDB Match", parsed, is_tv)
                return

            best_match = results[0]
            official_title = best_match.get("title") or best_match.get("name", parsed.title)
            release_date = best_match.get("release_date") or best_match.get("first_air_date", "")
            official_year = release_date[:4] if release_date else parsed.year
            
            ep_full_info = ""
            if is_tv and parsed.season is not None and parsed.episode is not None:
                try:
                    season_data = client.season_details(best_match["id"], parsed.season)
                    for ep in season_data.get("episodes", []):
                        if ep.get("episode_number") == parsed.episode:
                            ep_name = ep.get("name", f"Episode {parsed.episode}")
                            ep_full_info = f"S{parsed.season:02d}E{parsed.episode:02d} - {ep_name}"
                            break
                except:
                    ep_full_info = f"S{parsed.season:02d}E{parsed.episode:02d}"

            pil_img = None
            poster_path = best_match.get("poster_path")
            if poster_path and _HAVE_PIL:
                img_bytes = client.fetch_bytes(client.poster_url(poster_path, size="w342"))
                pil_img = Image.open(io.BytesIO(img_bytes))

            self.after(0, self._apply_tmdb_success, official_title, official_year, ep_full_info, pil_img)
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
            self._current_poster_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(disp_width, disp_height))
            try: self.preview_poster_label.configure(image=self._current_poster_img, fg_color="transparent")
            except: pass
        else:
            self._current_poster_img = None
            self.preview_poster_label.configure(text="[ No Poster Found ]", fg_color=BG_MAIN)

        search_query = display_text.replace(" ", "+")
        self.btn_trailer_preview.configure(state="normal", text="▶ Watch Trailer", 
            command=lambda: webbrowser.open(f"https://www.youtube.com/results?search_query={search_query}+official+trailer"))

    def _apply_tmdb_error(self, error_msg, parsed, is_tv):
        self._safe_clear_image(f"[ {error_msg} ]")
        try: self.preview_poster_label.configure(fg_color=BG_MAIN)
        except: pass
        
        display_text = f"{parsed.title}" + (f" ({parsed.year})" if parsed.year else "")
        self.lbl_preview_name.configure(text=display_text)
        ep_info = ""
        if is_tv and parsed.season is not None:
             ep_info = f"S{parsed.season:02d}E{parsed.episode:02d}" + (f" - {parsed.episode_title}" if parsed.episode_title else "")
        self.lbl_preview_ep_info.configure(text=ep_info)
        
        search_query = display_text.replace(" ", "+")
        self.btn_trailer_preview.configure(state="normal", text="▶ Search Trailer", 
            command=lambda: webbrowser.open(f"https://www.youtube.com/results?search_query={search_query}+trailer"))

    def log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    def run_process(self, dry_run: bool):
        if not self.current_paths: return
        self.clear_log()
        self.btn_dry_run.configure(state="disabled")
        self.btn_run.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._process_thread, args=(dry_run,), daemon=True).start()

    def _process_thread(self, dry_run: bool):
        fmt_string = self.format_entry.get()
        if self.chk_clean.get():
            for d in self.target_dirs:
                if d.exists() and d.is_dir():
                    self.log(f"Cleaning junk in: {d.name}...")
                    clean_artifacts(d, dry_run)

        client = TMDBClient(api_key=self.tmdb_api_key) if self.tmdb_api_key else None

        for i, file_path in enumerate(self.current_paths):
            if file_path not in self.file_statuses:
                self.file_statuses[file_path] = {}
                
            self.log(f"[{i+1}/{len(self.current_paths)}] Processing: {file_path.name}")
            try:
                is_tv = self._get_smart_is_tv(file_path)
                parsed = parse_filename(file_path, is_tv)
                
                if client:
                    results = client.search_tv_all(parsed.title, parsed.year) if is_tv else client.search_movie_all(parsed.title, parsed.year)
                    if results:
                        match = results[0]
                        parsed.title = match.get("title") or match.get("name", parsed.title)
                        rel_date = match.get("release_date") or match.get("first_air_date", "")
                        if rel_date: parsed.year = rel_date[:4]
                        
                        if is_tv and parsed.season is not None and parsed.episode is not None:
                            try:
                                s_data = client.season_details(match["id"], parsed.season)
                                for ep in s_data.get("episodes", []):
                                    if ep.get("episode_number") == parsed.episode:
                                        parsed.episode_title = "".join(c for c in ep.get("name", "") if c not in '<>:"\\|?*')
                                        break
                            except: pass

                ny = f"{parsed.title} ({parsed.year})" if parsed.year else parsed.title
                clean_ny = "".join(c for c in ny if c not in '<>:"\\|?*')
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
                    (r"(?i)\{en\}", en)
                ]
                
                for pat, val in replacements:
                    new_rel = re.sub(pat, lambda m, v=val: v, new_rel)
                
                clean_p = "".join(c for c in new_rel if c not in '<>:"\\|?*')
                
                parts = clean_p.split('/')
                if parts:
                    parts[-1] += file_path.suffix.lower()
                    
                base_d = self.target_dirs[0] if self.target_dirs else file_path.parent
                dest_path = base_d / Path(*parts)
                
                self.log(f" -> Renaming to: {dest_path.name}")
                req_post = self.chk_poster.get() if not is_tv else (self.chk_series_poster.get() or self.chk_season_poster.get())
                if not dry_run:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.rename(dest_path)
                    self.file_statuses[file_path]["rename"] = "done"
                
                if req_post and not dry_run:
                    self.log(" -> Fetching poster from TMDB...")
                    try:
                        tmdb = TMDBClient(self.tmdb_api_key)
                        results = tmdb.search_tv_all(parsed.title, parsed.year) if is_tv else tmdb.search_movie_all(parsed.title, parsed.year)
                        
                        if results and results[0].get("poster_path"):
                            series_root = dest_path.parent
                            if is_tv and len(parts) > 1:
                                best_idx = 0
                                for idx, p in enumerate(parts[:-1]):
                                    if parsed.title.lower() in p.lower():
                                        best_idx = idx
                                        break
                                series_root = base_d / Path(*parts[:best_idx+1])

                            if (not is_tv and self.chk_poster.get()) or (is_tv and self.chk_series_poster.get()):
                                img_url = tmdb.poster_url(results[0]["poster_path"], "w780")
                                poster_data = tmdb.fetch_bytes(img_url)
                                poster_name = f"{clean_ny} - Series Poster.jpg" if is_tv else f"{clean_ny} - Poster.jpg"
                                poster_dest = series_root / poster_name
                                
                                if not poster_dest.exists():
                                    poster_dest.parent.mkdir(parents=True, exist_ok=True)
                                    poster_dest.write_bytes(poster_data)
                                    
                            if is_tv and self.chk_season_poster.get() and parsed.season is not None:
                                tv_id = results[0]["id"]
                                season_data = tmdb.season_details(tv_id, parsed.season)
                                if season_data and season_data.get("poster_path"):
                                    s_url = tmdb.poster_url(season_data["poster_path"], "w780")
                                    s_data = tmdb.fetch_bytes(s_url)
                                    s_dest = dest_path.parent / f"Season {parsed.season:02d} Poster.jpg"
                                    
                                    if not s_dest.exists():
                                        s_dest.parent.mkdir(parents=True, exist_ok=True)
                                        s_dest.write_bytes(s_data)

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
                    success, _ = download_trailer(yt_url, trailer_dest)
                    self.file_statuses[file_path]["trailer"] = "done" if success else "error"
                
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
                key=lambda p: len(p.parts), reverse=True
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
        self.after(0, self._process_finished)

    def _process_finished(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1)
        self.btn_dry_run.configure(state="normal")
        self.btn_run.configure(state="normal")        
# ==============================================================================
# SECTION 9: ENTRYPOINT
# ==============================================================================

def run_cli(argv: None = None) -> int:
    if len(sys.argv) <= 1:
        app = App()
        app.mainloop()
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))