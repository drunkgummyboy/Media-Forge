from pathlib import Path

from mediaforge.db.database import SCHEMA_VERSION, Database


async def _scalar(db: Database, sql: str) -> object:
    async with db.conn.execute(sql) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def test_open_sets_wal_and_version(db: Database) -> None:
    assert await _scalar(db, "PRAGMA journal_mode") == "wal"
    assert await _scalar(db, "PRAGMA user_version") == SCHEMA_VERSION == 2
    assert await _scalar(db, "PRAGMA foreign_keys") == 1


async def test_open_twice_is_idempotent(tmp_path: Path) -> None:
    first = await Database.open(tmp_path / "x.db")
    await first.close()
    second = await Database.open(tmp_path / "x.db")
    try:
        assert await _scalar(second, "PRAGMA user_version") == SCHEMA_VERSION
    finally:
        await second.close()


async def test_open_creates_parent_folder(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "a" / "b" / "x.db")
    await database.close()
    assert (tmp_path / "a" / "b" / "x.db").exists()
