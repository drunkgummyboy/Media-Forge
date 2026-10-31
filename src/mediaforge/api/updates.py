"""Self-update from GitHub: compare versions, download the branch archive, stage or install it.

The file work (unpacking, swapping) lives in platform/update_files.py, which the launcher also
uses after shutdown; this module only talks to GitHub and runs that work in a thread.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp

import mediaforge
from mediaforge.models.update import UpdateInfo
from mediaforge.platform.update_files import (
    UpdateFileError,
    install_archive,
    is_development_copy,
    is_newer,
    parse_version,
    stage_archive,
)

log = logging.getLogger("mediaforge.update")

GITHUB_REPO = "drunkgummyboy/Media-Forge"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}/src/mediaforge/__init__.py"
ARCHIVE_URL = f"https://codeload.github.com/{GITHUB_REPO}/zip/refs/heads/{BRANCH}"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
INSTALL_DIR = Path(mediaforge.__file__).resolve().parents[2]  # <install>\src\mediaforge
_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=30)
_DEV_COPY = "This is a development copy; it is never overwritten by an update."


class UpdateError(Exception):
    """An update check or install that failed; the message is shown in the app."""


class UpdateService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        install_dir: Path = INSTALL_DIR,
        *,
        current: str = mediaforge.__version__,
        version_url: str = VERSION_URL,
        archive_url: str = ARCHIVE_URL,
    ) -> None:
        self._session = session
        self._dir = install_dir
        self._current = current
        self._version_url = version_url
        self._archive_url = archive_url

    @property
    def current(self) -> str:
        return self._current

    @property
    def install_dir(self) -> Path:
        return self._dir

    @property
    def installable(self) -> bool:
        return not is_development_copy(self._dir)

    async def check(self) -> UpdateInfo:
        """The newest version on GitHub. Raises UpdateError when GitHub can't tell."""
        try:
            async with self._session.get(
                self._version_url, headers={"Cache-Control": "no-cache"}, timeout=_TIMEOUT
            ) as response:
                status = response.status
                text = await response.text(errors="replace") if status == 200 else ""
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise UpdateError("Could not reach GitHub.") from exc
        if status == 404:
            raise UpdateError("GitHub doesn't have a MediaForge version yet.")
        if status != 200:
            raise UpdateError(f"GitHub answered HTTP {status}.")
        latest = parse_version(text)
        if latest is None:
            raise UpdateError("GitHub doesn't have a MediaForge version yet.")
        return UpdateInfo(
            current=self._current,
            latest=latest,
            available=is_newer(latest, self._current),
            installable=self.installable,
        )

    async def install(self) -> str:
        """Download the latest version and swap it in. Returns the version now installed.

        The running process keeps the old code in memory: restart right after this returns.
        """
        if not self.installable:
            raise UpdateError(_DEV_COPY)
        data = await self._download()
        try:
            version = await asyncio.to_thread(install_archive, data, self._dir)
        except (UpdateFileError, OSError) as exc:
            raise UpdateError(str(exc) or "Could not install the update.") from exc
        log.info("Updated from %s to %s", self._current, version)
        return version

    async def stage(self) -> str:
        """Download and unpack the latest version, ready to be swapped in once MediaForge has
        closed (the launcher calls `apply_staged`). Returns the staged version."""
        if not self.installable:
            raise UpdateError(_DEV_COPY)
        data = await self._download()
        try:
            version = await asyncio.to_thread(
                stage_archive, data, self._dir, newer_than=self._current
            )
        except UpdateFileError as exc:
            raise UpdateError(str(exc)) from exc
        except OSError as exc:
            log.warning("Could not unpack the update: %r", exc)
            raise UpdateError("Could not unpack the update.") from exc
        log.info("Update %s is ready; it is installed when MediaForge closes", version)
        return version

    async def _download(self) -> bytes:
        try:
            async with self._session.get(self._archive_url, timeout=_TIMEOUT) as response:
                if response.status != 200:
                    raise UpdateError(f"Download failed: GitHub answered HTTP {response.status}.")
                body = bytearray()
                async for chunk in response.content.iter_chunked(256 * 1024):
                    body += chunk
                    if len(body) > MAX_ARCHIVE_BYTES:
                        raise UpdateError("Download failed: the archive is unexpectedly large.")
                return bytes(body)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise UpdateError("Download failed: could not reach GitHub.") from exc
