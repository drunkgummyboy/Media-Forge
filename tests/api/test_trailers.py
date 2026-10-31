import hashlib
import io
import json
import sys
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import pytest_asyncio

from mediaforge.api.trailers import (
    FORMAT,
    ToolInstallError,
    TrailerService,
    sha256_for,
    youtube_search_url,
)
from mediaforge.db.database import Database
from mediaforge.db.settings import AppSettings, SettingsRepo
from mediaforge.models.plan import PlannedTrailer

from helpers.fake_tmdb import Reply, running_fake

FAKE_YTDLP = str(Path(__file__).parents[1] / "helpers" / "fake_ytdlp.py")


@pytest_asyncio.fixture
async def settings(tmp_path: Path) -> AsyncIterator[SettingsRepo]:
    db = await Database.open(tmp_path / "s.db")
    try:
        yield SettingsRepo(db)
    finally:
        await db.close()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest_asyncio.fixture
async def web() -> AsyncIterator[Any]:
    async with running_fake() as f:
        yield f


def trailer(folder: Path, title: str = "Dune Part Two", year: int | None = 2024) -> PlannedTrailer:
    ny = f"{title} ({year})" if year else title
    return PlannedTrailer(
        query=f"ytsearch1:{title} {year} official trailer",
        title=title,
        year=year,
        target_stem=folder / f"{ny} - Trailer",
    )


def service(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo, **kw: Any
) -> TrailerService:
    kw.setdefault("ytdlp_command", [sys.executable, FAKE_YTDLP])
    return TrailerService(tmp_path / "tools", session, settings, **kw)


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


async def test_download_success_returns_mp4(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo
) -> None:
    result = await service(tmp_path, session, settings).download(trailer(tmp_path))
    assert result == tmp_path / "Dune Part Two (2024) - Trailer.mp4"
    assert result.read_bytes() == b"fake trailer"


async def test_download_failure_writes_url_shortcut(
    tmp_path: Path,
    session: aiohttp.ClientSession,
    settings: SettingsRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FAIL", "1")
    monkeypatch.setenv("FAKE_ARGV", str(tmp_path / "argv.log"))
    result = await service(tmp_path, session, settings).download(trailer(tmp_path))
    assert result == tmp_path / "Dune Part Two (2024) - Trailer.url"
    text = result.read_text(encoding="utf-8")
    assert "[InternetShortcut]" in text
    assert (
        "URL=https://www.youtube.com/results?search_query=Dune+Part+Two+2024+official+trailer"
        in text
    )
    # one failed try, one self-update, one retry
    assert [("-U" in c) for c in calls(tmp_path / "argv.log")] == [False, True, False]


async def test_existing_trailer_is_not_redownloaded(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo
) -> None:
    (tmp_path / "Dune Part Two (2024) - Trailer.MP4").write_bytes(b"mine")
    assert await service(tmp_path, session, settings).download(trailer(tmp_path)) is None
    assert (tmp_path / "Dune Part Two (2024) - Trailer.MP4").read_bytes() == b"mine"


async def test_command_has_expected_args(
    tmp_path: Path,
    session: aiohttp.ClientSession,
    settings: SettingsRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_ARGV", str(tmp_path / "argv.log"))
    bin_dir = tmp_path / "tools" / "ffmpeg" / "bin"
    svc = service(tmp_path, session, settings)
    await svc.download(trailer(tmp_path))
    bin_dir.mkdir(parents=True)
    (bin_dir / "ffmpeg.exe").write_bytes(b"x")
    (tmp_path / "other").mkdir()
    await svc.download(trailer(tmp_path / "other"))
    without, with_ffmpeg = calls(tmp_path / "argv.log")
    assert "--ffmpeg-location" not in without
    assert without[without.index("-f") + 1] == "best[ext=mp4]/best"
    assert with_ffmpeg[with_ffmpeg.index("-f") + 1] == FORMAT
    assert with_ffmpeg[with_ffmpeg.index("--merge-output-format") + 1] == "mp4"
    assert with_ffmpeg[with_ffmpeg.index("--ffmpeg-location") + 1] == str(bin_dir)
    assert with_ffmpeg[with_ffmpeg.index("-o") + 1].endswith(
        "Dune Part Two (2024) - Trailer.%(ext)s"
    )
    assert with_ffmpeg[-1] == "ytsearch1:Dune Part Two 2024 official trailer"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _sums(name: str, data: bytes) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\nffff  other.exe\n".encode()


async def test_install_verifies_checksum_and_extracts_only_bin(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo, web: Any
) -> None:
    exe = b"MZ fake yt-dlp"
    zipped = _zip(
        {
            "ffmpeg-master-latest-win64-gpl-shared/bin/ffmpeg.exe": b"ff",
            "ffmpeg-master-latest-win64-gpl-shared/bin/avcodec-61.dll": b"dll",
            "ffmpeg-master-latest-win64-gpl-shared/doc/readme.txt": b"doc",
        }
    )
    web.add("/y/yt-dlp.exe", Reply(body=exe))
    web.add("/y/SHA2-256SUMS", Reply(body=_sums("yt-dlp.exe", exe)))
    web.add("/f/ff.zip", Reply(body=zipped))
    web.add("/f/checksums.sha256", Reply(body=_sums("ff.zip", zipped)))
    svc = service(
        tmp_path,
        session,
        settings,
        ytdlp_url=f"{web.url}/y/yt-dlp.exe",
        ytdlp_sums_url=f"{web.url}/y/SHA2-256SUMS",
        ffmpeg_url=f"{web.url}/f/ff.zip",
        ffmpeg_sums_url=f"{web.url}/f/checksums.sha256",
    )
    progress: list[tuple[str, float]] = []
    await svc.install_tools(lambda step, frac: progress.append((step, frac)))
    tools = tmp_path / "tools"
    assert (tools / "yt-dlp.exe").read_bytes() == exe
    assert (tools / "ffmpeg" / "bin" / "ffmpeg.exe").read_bytes() == b"ff"
    assert (tools / "ffmpeg" / "bin" / "avcodec-61.dll").exists()
    assert not (tools / "ffmpeg" / "doc").exists()
    assert not list(tools.glob("*.part")) and not list(tools.glob("*.zip"))
    assert svc.tools_present()
    assert progress and progress[-1][1] == 1.0
    assert (await settings.load()).trailer_tools_ok


async def test_install_rejects_bad_checksum(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo, web: Any
) -> None:
    web.add("/y/yt-dlp.exe", Reply(body=b"tampered"))
    web.add("/y/SHA2-256SUMS", Reply(body=_sums("yt-dlp.exe", b"original")))
    svc = service(
        tmp_path,
        session,
        settings,
        ytdlp_url=f"{web.url}/y/yt-dlp.exe",
        ytdlp_sums_url=f"{web.url}/y/SHA2-256SUMS",
    )
    with pytest.raises(ToolInstallError):
        await svc.install_tools(lambda step, frac: None)
    assert not (tmp_path / "tools" / "yt-dlp.exe").exists()
    assert not list((tmp_path / "tools").glob("*"))


async def test_maybe_update_respects_interval(
    tmp_path: Path,
    session: aiohttp.ClientSession,
    settings: SettingsRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_ARGV", str(tmp_path / "argv.log"))
    svc = service(tmp_path, session, settings)
    await settings.save(AppSettings(ytdlp_checked_at=time.time()))
    await svc.maybe_update()
    assert calls(tmp_path / "argv.log") == []
    await settings.save(AppSettings(ytdlp_checked_at=0))
    await svc.maybe_update()
    assert calls(tmp_path / "argv.log") == [["-U"]]
    assert (await settings.load()).ytdlp_checked_at > 0


def test_sha256_for_parses_sums() -> None:
    text = "abc123  yt-dlp.exe\ndef456 *yt-dlp_x86.exe\n"
    assert sha256_for(text, "yt-dlp.exe") == "abc123"
    assert sha256_for(text, "yt-dlp_x86.exe") == "def456"
    assert sha256_for(text, "missing") is None


def test_youtube_search_url() -> None:
    assert youtube_search_url("Fast & Furious", None) == (
        "https://www.youtube.com/results?search_query=Fast+%26+Furious+official+trailer"
    )


async def test_cancel_kills_ytdlp(
    tmp_path: Path,
    session: aiohttp.ClientSession,
    settings: SettingsRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review I4: a cancelled download must not keep writing into the user's folder.
    import asyncio

    monkeypatch.setenv("FAKE_SLEEP", "2")
    task = asyncio.create_task(service(tmp_path, session, settings).download(trailer(tmp_path)))
    await asyncio.sleep(0.8)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)
    assert not (tmp_path / "Dune Part Two (2024) - Trailer.mp4").exists()


async def test_install_twice_at_once_downloads_once(
    tmp_path: Path, session: aiohttp.ClientSession, settings: SettingsRepo, web: Any
) -> None:
    import asyncio

    exe = b"MZ fake yt-dlp"
    zipped = _zip({"ff/bin/ffmpeg.exe": b"ff"})
    web.add("/y/yt-dlp.exe", Reply(body=exe))
    web.add("/y/SHA2-256SUMS", Reply(body=_sums("yt-dlp.exe", exe)))
    web.add("/f/ff.zip", Reply(body=zipped))
    web.add("/f/checksums.sha256", Reply(body=_sums("ff.zip", zipped)))
    svc = service(
        tmp_path,
        session,
        settings,
        ytdlp_url=f"{web.url}/y/yt-dlp.exe",
        ytdlp_sums_url=f"{web.url}/y/SHA2-256SUMS",
        ffmpeg_url=f"{web.url}/f/ff.zip",
        ffmpeg_sums_url=f"{web.url}/f/checksums.sha256",
        ytdlp_command=None,
    )
    await asyncio.gather(svc.install_tools(lambda s, f: None), svc.install_tools(lambda s, f: None))
    assert len(web.requests_to("/y/yt-dlp.exe")) == 1
    assert svc.tools_present()
