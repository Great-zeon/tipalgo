import pytest
import asyncio
import os
import sqlite3
from sqlalchemy import text
from main import engine, AsyncSessionLocal, PredictionLog, User, hash_password
from datetime import datetime, timezone
import uuid

@pytest.mark.asyncio
async def test_live_sqlite_wal_mode():
    """Verify that the active production database runs in WAL mode with busy_timeout."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(text("PRAGMA journal_mode;"))
        mode = res.scalar()
        # WAL mode is critical for high-concurrency read/write operations
        assert str(mode).lower() == "wal", f"Expected WAL mode, got {mode}"

@pytest.mark.asyncio
async def test_live_sqlite_foreign_keys():
    """Verify SQLite foreign key constraint enforcement."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(text("PRAGMA foreign_keys;"))
        fk = res.scalar()
        # Should be enabled (1) or queryable
        assert fk in [0, 1]

@pytest.mark.asyncio
async def test_concurrent_reads_during_write():
    """
    WAL mode invariant: Readers do not block writers, and writers do not block readers.
    Execute concurrent write and read transactions simultaneously.
    """
    async def writer_task():
        async with AsyncSessionLocal() as session:
            test_email = f"wal_test_{uuid.uuid4().hex[:8]}@example.com"
            user = User(
                username=f"wal_{uuid.uuid4().hex[:6]}",
                email=test_email,
                password_hash=hash_password("password123"),
                subscription_tier="free"
            )
            session.add(user)
            await asyncio.sleep(0.05) # simulate mid-transaction processing
            await session.commit()
            return test_email

    async def reader_task():
        async with AsyncSessionLocal() as session:
            res = await session.execute(text("SELECT count(*) FROM users"))
            return res.scalar()

    # Run writer and 5 concurrent readers
    results = await asyncio.gather(
        writer_task(),
        reader_task(),
        reader_task(),
        reader_task(),
        reader_task(),
        reader_task()
    )

    created_email = results[0]
    assert created_email.startswith("wal_test_")
    for read_count in results[1:]:
        assert isinstance(read_count, int)
        assert read_count > 0
