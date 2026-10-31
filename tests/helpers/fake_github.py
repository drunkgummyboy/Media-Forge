"""A stand-in for GitHub's raw-file and branch-download addresses, for update tests.

The in-process server is the same one the TMDB tests use; this adds the two paths the
updater asks for and a builder for archives shaped like GitHub's.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from helpers.fake_tmdb import FakeTmdb, Reply, running_fake

VERSION_PATH = "/drunkgummyboy/Media-Forge/main/src/mediaforge/__init__.py"
ARCHIVE_PATH = "/drunkgummyboy/Media-Forge/zip/refs/heads/main"


def github_archive(version: str, top: str = "Media-Forge-main") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"{top}/main.py", f"# launcher {version}\n")
        z.writestr(f"{top}/pyproject.toml", "[project]\n")
        z.writestr(f"{top}/src/mediaforge/__init__.py", f'__version__ = "{version}"\n')
        z.writestr(f"{top}/tests/test_x.py", "# not copied\n")
    return buffer.getvalue()


@dataclass
class FakeGithub:
    server: FakeTmdb

    @property
    def version_url(self) -> str:
        return self.server.url + VERSION_PATH

    @property
    def archive_url(self) -> str:
        return self.server.url + ARCHIVE_PATH

    def publish(self, version: str) -> None:
        """The page now holds this version: both addresses answer with it."""
        source = f'"""MediaForge."""\n\n__version__ = "{version}"\n'.encode()
        self.server.replies[VERSION_PATH] = [Reply(body=source)]
        self.server.replies[ARCHIVE_PATH] = [Reply(body=github_archive(version))]


@asynccontextmanager
async def running_github() -> AsyncIterator[FakeGithub]:
    async with running_fake() as server:
        yield FakeGithub(server)
