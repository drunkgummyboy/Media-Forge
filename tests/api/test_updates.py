from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

from mediaforge.api import updates
from mediaforge.api.updates import UpdateError, UpdateService
from mediaforge.platform.update_files import STAGING_DIR

from helpers.fake_github import ARCHIVE_PATH, FakeGithub, running_github
from helpers.fake_tmdb import Reply


@pytest_asyncio.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest_asyncio.fixture
async def github() -> AsyncIterator[FakeGithub]:
    async with running_github() as g:
        yield g


def install_dir(tmp_path: Path) -> Path:
    root = tmp_path / "MediaForge"
    (root / "src" / "mediaforge").mkdir(parents=True)
    (root / "main.py").write_text("# launcher 1.1.0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "src" / "mediaforge" / "__init__.py").write_text(
        '__version__ = "1.1.0"\n', encoding="utf-8"
    )
    return root


def service(
    session: aiohttp.ClientSession, github: FakeGithub, root: Path, current: str = "1.1.0"
) -> UpdateService:
    return UpdateService(
        session,
        root,
        current=current,
        version_url=github.version_url,
        archive_url=github.archive_url,
    )


async def test_check_reports_newer_version(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.2.0")
    info = await service(session, github, install_dir(tmp_path)).check()
    assert (info.current, info.latest, info.available, info.installable) == (
        "1.1.0",
        "1.2.0",
        True,
        True,
    )
    [seen] = github.server.requests_to("/drunkgummyboy/Media-Forge/main/src/mediaforge/__init__.py")
    assert seen.headers.get("Cache-Control") == "no-cache"


async def test_check_same_version_is_not_available(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.1.0")
    info = await service(session, github, install_dir(tmp_path)).check()
    assert not info.available


async def test_check_404_says_no_version_yet(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    with pytest.raises(UpdateError, match="doesn't have a MediaForge version"):
        await service(session, github, install_dir(tmp_path)).check()


async def test_check_unreachable_raises_update_error(
    session: aiohttp.ClientSession, tmp_path: Path
) -> None:
    svc = UpdateService(
        session,
        install_dir(tmp_path),
        version_url="http://127.0.0.1:9/nothing",
        archive_url="http://127.0.0.1:9/nothing",
    )
    with pytest.raises(UpdateError, match="Could not reach GitHub"):
        await svc.check()


async def test_stage_downloads_and_stages(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.2.0")
    root = install_dir(tmp_path)
    assert await service(session, github, root).stage() == "1.2.0"
    assert (root / STAGING_DIR / "ready").read_text(encoding="utf-8") == "1.2.0"


async def test_stage_refuses_an_archive_that_is_not_newer(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.1.0")
    with pytest.raises(UpdateError):
        await service(session, github, install_dir(tmp_path)).stage()


async def test_install_swaps_the_files_in(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.2.0")
    root = install_dir(tmp_path)
    assert await service(session, github, root).install() == "1.2.0"
    assert (root / "main.py").read_text(encoding="utf-8") == "# launcher 1.2.0\n"


async def test_download_over_limit_is_refused(
    session: aiohttp.ClientSession,
    github: FakeGithub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github.publish("1.2.0")
    monkeypatch.setattr(updates, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(UpdateError, match="unexpectedly large"):
        await service(session, github, install_dir(tmp_path)).stage()


async def test_download_error_status_is_reported(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.2.0")
    github.server.replies[ARCHIVE_PATH] = [Reply(status=503, body=b"busy")]
    with pytest.raises(UpdateError, match="HTTP 503"):
        await service(session, github, install_dir(tmp_path)).stage()


async def test_development_copy_never_stages_or_installs(
    session: aiohttp.ClientSession, github: FakeGithub, tmp_path: Path
) -> None:
    github.publish("1.2.0")
    root = install_dir(tmp_path)
    (root / "tests").mkdir()
    svc = service(session, github, root)
    assert not svc.installable
    assert (await svc.check()).installable is False
    with pytest.raises(UpdateError, match="development copy"):
        await svc.stage()
    with pytest.raises(UpdateError, match="development copy"):
        await svc.install()
    assert not github.server.requests_to(ARCHIVE_PATH)
