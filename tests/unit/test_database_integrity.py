import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from main import Base, User, HistoricalMatch, LeagueTable, BetSlip, hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def test_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    
    await engine.dispose()

@pytest.mark.asyncio
async def test_user_unique_email_constraint(test_db):
    """Verify that duplicate user emails are rejected by unique constraint."""
    u1 = User(
        username="user_one",
        email="test_unique@example.com",
        password_hash=hash_password("pw123456"),
        bankroll=50000.0
    )
    test_db.add(u1)
    await test_db.commit()
    
    # Try inserting duplicate email
    u2 = User(
        username="user_two",
        email="test_unique@example.com",
        password_hash=hash_password("pw654321"),
        bankroll=50000.0
    )
    test_db.add(u2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()

@pytest.mark.asyncio
async def test_fixture_unique_external_id(test_db):
    """Verify that duplicate fixture external_id is rejected by unique constraint."""
    f1 = HistoricalMatch(
        external_id="EXT-1001",
        home_team="Team A",
        away_team="Team B",
        league_id="PL",
        match_date=datetime.now(timezone.utc),
        home_odds=2.0,
        draw_odds=3.2,
        away_odds=3.5
    )
    test_db.add(f1)
    await test_db.commit()
    
    # Duplicate external_id
    f2 = HistoricalMatch(
        external_id="EXT-1001",
        home_team="Team C",
        away_team="Team D",
        league_id="PL",
        match_date=datetime.now(timezone.utc),
        home_odds=2.1,
        draw_odds=3.1,
        away_odds=3.6
    )
    test_db.add(f2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()

@pytest.mark.asyncio
async def test_transaction_rollback(test_db):
    """Verify that an interrupted or aborted transaction cleanly rolls back."""
    f = HistoricalMatch(
        external_id="EXT-ROLLBACK-1",
        home_team="Arsenal",
        away_team="Chelsea",
        league_id="PL",
        match_date=datetime.now(timezone.utc)
    )
    test_db.add(f)
    await test_db.flush()
    
    # Rollback instead of commit
    await test_db.rollback()
    
    res = await test_db.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == "EXT-ROLLBACK-1"))
    assert res.scalar_one_or_none() is None
