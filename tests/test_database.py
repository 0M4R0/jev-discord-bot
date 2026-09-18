"""Basic database tests."""

import pytest
import pytest_asyncio

from database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    path = str(tmp_path / "test.db")
    d = Database(path)
    await d.connect()
    yield d
    await d.close()


@pytest.mark.asyncio
async def test_guild_settings_defaults(db):
    s = await db.get_guild_settings(123)
    assert s.guild_id == 123
    assert s.tier1_threshold == 0.95
    assert s.mod_log_channel_id is None


@pytest.mark.asyncio
async def test_offense_cycle(db):
    oid = await db.add_offense(1, 99, "bad link http://scam", 42, "WARN_1_DM", 0.97)
    assert oid > 0
    assert await db.count_active_offenses(1, 99) == 1
    offense = await db.pardon_latest(1, 99)
    assert offense is not None
    assert offense.status == "PARDONED"
    assert await db.count_active_offenses(1, 99) == 0


@pytest.mark.asyncio
async def test_false_flags(db):
    await db.add_false_flag(1, "legit meme link")
    flags = await db.get_recent_false_flags(1)
    assert len(flags) == 1
    assert "meme" in flags[0]
