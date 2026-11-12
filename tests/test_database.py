from __future__ import annotations

import pytest

from mediabot.database import Database


@pytest.mark.asyncio
async def test_database_records_users_and_downloads(tmp_path) -> None:
    db = Database(str(tmp_path / "test.sqlite3"))
    await db.open()
    try:
        await db.touch_user(123)
        await db.record_download(123, "video")
        users, total, today = await db.stats()
        assert users == 1
        assert total == 1
        assert today == 1
    finally:
        await db.close()
