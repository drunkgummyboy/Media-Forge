from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from mediaforge.db.database import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = await Database.open(tmp_path / "t.db")
    try:
        yield database
    finally:
        await database.close()
