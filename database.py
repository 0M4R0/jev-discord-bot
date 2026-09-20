"""SQLite persistence for guild settings, offenses, and false-flag memory."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import aiosqlite

from config import DATABASE_PATH


@dataclass
class GuildSettings:
    guild_id: int
    mod_log_channel_id: int | None = None
    tier1_threshold: float = 0.95
    tier2_threshold: float = 0.70
    first_timeout_mins: int = 10
    subsequent_timeout_mins: int = 60


@dataclass
class Offense:
    id: int
    guild_id: int
    user_id: int
    message_content: str
    channel_id: int
    action: str
    status: str  # ACTIVE | PARDONED | BANNED
    confidence: float
    created_at: str
    resolved_at: str | None = None


class Database:
    def __init__(self, path: str = DATABASE_PATH) -> None:
        self.path = path

    async def connect(self) -> None:
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self._migrate()

    async def close(self) -> None:
        if hasattr(self, "db"):
            await self.db.close()

    async def _migrate(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                mod_log_channel_id INTEGER,
                tier1_threshold REAL DEFAULT 0.95,
                tier2_threshold REAL DEFAULT 0.70,
                first_timeout_mins INTEGER DEFAULT 10,
                subsequent_timeout_mins INTEGER DEFAULT 60
            );

            CREATE TABLE IF NOT EXISTS offenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_content TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS false_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                message_content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_offenses_guild_user
                ON offenses(guild_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_false_flags_guild
                ON false_flags(guild_id);
            """
        )
        await self.db.commit()

    async def get_guild_settings(self, guild_id: int) -> GuildSettings:
        async with self.db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            settings = GuildSettings(guild_id=guild_id)
            await self.save_guild_settings(settings)
            return settings
        return GuildSettings(
            guild_id=row["guild_id"],
            mod_log_channel_id=row["mod_log_channel_id"],
            tier1_threshold=row["tier1_threshold"],
            tier2_threshold=row["tier2_threshold"],
            first_timeout_mins=row["first_timeout_mins"],
            subsequent_timeout_mins=row["subsequent_timeout_mins"],
        )

    async def save_guild_settings(self, settings: GuildSettings) -> None:
        await self.db.execute(
            """
            INSERT INTO guild_settings (
                guild_id, mod_log_channel_id, tier1_threshold, tier2_threshold,
                first_timeout_mins, subsequent_timeout_mins
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                mod_log_channel_id = excluded.mod_log_channel_id,
                tier1_threshold = excluded.tier1_threshold,
                tier2_threshold = excluded.tier2_threshold,
                first_timeout_mins = excluded.first_timeout_mins,
                subsequent_timeout_mins = excluded.subsequent_timeout_mins
            """,
            (
                settings.guild_id,
                settings.mod_log_channel_id,
                settings.tier1_threshold,
                settings.tier2_threshold,
                settings.first_timeout_mins,
                settings.subsequent_timeout_mins,
            ),
        )
        await self.db.commit()

    async def count_active_offenses(self, guild_id: int, user_id: int) -> int:
        async with self.db.execute(
            """
            SELECT COUNT(*) AS cnt FROM offenses
            WHERE guild_id = ? AND user_id = ? AND status = 'ACTIVE'
            """,
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def add_offense(
        self,
        guild_id: int,
        user_id: int,
        message_content: str,
        channel_id: int,
        action: str,
        confidence: float,
    ) -> int:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = await self.db.execute(
            """
            INSERT INTO offenses (
                guild_id, user_id, message_content, channel_id,
                action, status, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (guild_id, user_id, message_content, channel_id, action, confidence, now),
        )
        await self.db.commit()
        return cursor.lastrowid or 0

    async def pardon_latest(self, guild_id: int, user_id: int) -> Offense | None:
        async with self.db.execute(
            """
            SELECT * FROM offenses
            WHERE guild_id = ? AND user_id = ? AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
            """,
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE offenses SET status = 'PARDONED', resolved_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        await self.db.commit()
        return Offense(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            message_content=row["message_content"],
            channel_id=row["channel_id"],
            action=row["action"],
            status="PARDONED",
            confidence=row["confidence"],
            created_at=row["created_at"],
            resolved_at=now,
        )

    async def mark_banned(self, guild_id: int, user_id: int) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await self.db.execute(
            """
            UPDATE offenses SET status = 'BANNED', resolved_at = ?
            WHERE guild_id = ? AND user_id = ? AND status = 'ACTIVE'
            """,
            (now, guild_id, user_id),
        )
        await self.db.commit()

    async def get_user_offenses(self, guild_id: int, user_id: int) -> list[Offense]:
        async with self.db.execute(
            """
            SELECT * FROM offenses
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 50
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Offense(
                id=r["id"],
                guild_id=r["guild_id"],
                user_id=r["user_id"],
                message_content=r["message_content"],
                channel_id=r["channel_id"],
                action=r["action"],
                status=r["status"],
                confidence=r["confidence"],
                created_at=r["created_at"],
                resolved_at=r["resolved_at"],
            )
            for r in rows
        ]

    async def add_false_flag(self, guild_id: int, message_content: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO false_flags (guild_id, message_content, created_at) VALUES (?, ?, ?)",
            (guild_id, message_content[:500], now),
        )
        await self.db.commit()

    async def get_recent_false_flags(self, guild_id: int, limit: int = 5) -> list[str]:
        async with self.db.execute(
            """
            SELECT message_content FROM false_flags
            WHERE guild_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [r["message_content"] for r in rows]

    async def export_feedback(self, guild_id: int) -> list[dict]:
        async with self.db.execute(
            """
            SELECT message_content, status, confidence, created_at, action
            FROM offenses WHERE guild_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


db_instance = Database()
