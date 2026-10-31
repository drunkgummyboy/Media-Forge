"""UI test fixtures.

Creating a fresh Tk root again and again in one process fails at random on Windows (Tcl
sometimes can't load its message catalogs), so every UI test shares one hidden root and
builds its widgets inside it. The real app creates exactly one root per process.
"""

import gc
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from mediaforge.app import Services, build_services, close_services
from mediaforge.platform.paths import app_paths
from mediaforge.ui import gc_guard
from mediaforge.ui.dnd_root import DnDCTk


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests\\ui\\" in str(item.path) or "tests/ui/" in str(item.path):
            item.add_marker(pytest.mark.ui)


@pytest.fixture(scope="session")
def tk_root() -> Iterator[DnDCTk]:
    root = DnDCTk()
    root.geometry("1200x800+4000+4000")
    gc_guard.start(root)
    yield root
    root.destroy()
    gc_guard.stop()


@pytest_asyncio.fixture
async def services(mediaforge_home: Path) -> AsyncIterator[Services]:
    paths = app_paths()
    paths.ensure()
    s = await build_services(paths, legacy_dirs=[])
    try:
        yield s
    finally:
        await close_services(s)
        gc.collect()  # on the Tk thread, never in a worker thread
