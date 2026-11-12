from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_downloads_created_at ON downloads(created_at);
            """
        )
        await self.connection.commit()

    async def touch_user(self, user_id: int) -> None:
        db = self._db()
        await db.execute(
            "INSERT INTO users(user_id) VALUES (?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen=datetime('now')",
            (user_id,),
        )
        await db.commit()

    async def record_download(self, user_id: int, mode: str) -> None:
        db = self._db()
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
        await db.execute("INSERT INTO downloads(user_id, mode) VALUES (?, ?)", (user_id, mode))
        await db.commit()

    async def stats(self) -> tuple[int, int, int]:
        db = self._db()
        users = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        total = await (await db.execute("SELECT COUNT(*) FROM downloads")).fetchone()
        today = await (
            await db.execute("SELECT COUNT(*) FROM downloads WHERE date(created_at)=date('now')")
        ).fetchone()
        return int(users[0]), int(total[0]), int(today[0])

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    def _db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open")
        return self.connection
