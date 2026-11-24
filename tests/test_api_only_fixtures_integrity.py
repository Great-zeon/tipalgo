import pytest
import json
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from main import app, AsyncSessionLocal, HistoricalMatch, PredictionLog, calendar_slate_cache, purge_synthetic_records_on_startup
from football_ingestion import FootballDataService, ProviderResult, LiveFixtureItem


@pytest.mark.asyncio
async def test_provider_insufficient_credits_returns_honest_error_and_zero_fixtures():
    """
    Forensic Verification:
    When the live football API provider returns HTTP 403 with credit exhaustion notice,
    FootballDataService MUST return an error ProviderResult with error_code='INSUFFICIENT_CREDITS',
    the required transparent user notice, and ZERO fixtures.
    """
    service = FootballDataService(api_key="mock_key_test", base_url="https://live-football-api.com/api/v1")
    
    mock_403_body = json.dumps({
        "success": False,
        "message": "Access denied. Possible reasons: Invalid key, insufficient credits, daily limit exceeded, or inactive account."
    })
    
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.text = AsyncMock(return_value=mock_403_body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    
    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        res = await service.fetch_live_fixtures_with_provenance("2026-11-24")
        
        assert res.success is False
        assert res.status_code == 403
        assert res.error_code == "INSUFFICIENT_CREDITS"
        assert "insufficient API credits" in res.user_message
        assert res.matches == []
        assert len(res.matches) == 0


@pytest.mark.asyncio
async def test_calendar_daily_slate_returns_transparent_error_on_credit_exhaustion():
    """
    Executive Directive Verification:
    The calendar daily slate endpoint must return honest error metadata and ZERO matches
    when the provider reports insufficient credits. It MUST NOT fall back to database records.
    """
    calendar_slate_cache.clear()
    
    mock_403_body = json.dumps({
        "success": False,
        "message": "Access denied. Possible reasons: Invalid key, insufficient credits, daily limit exceeded, or inactive account."
    })
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.text = AsyncMock(return_value=mock_403_body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    
    transport = ASGITransport(app=app)
    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/calendar/daily-slate?date=2026-11-24")
            assert resp.status_code == 200
            data = resp.json()
            
            assert data["status"] == "error"
            assert data["provider_status"] == "error"
            assert data["provider_error_code"] == "INSUFFICIENT_CREDITS"
            assert "insufficient API credits" in data["provider_message"]
            assert data["total_fixtures"] == 0
            assert data["matches_count"] == 0
            assert data["matches"] == []


@pytest.mark.asyncio
async def test_database_is_never_used_as_fallback_for_daily_slate():
    """
    Strict Database Separation:
    Even if synthetic or residual fixtures exist in historical_matches,
    /api/calendar/daily-slate must NEVER read or serve them.
    """
    calendar_slate_cache.clear()
    target_date = "2026-11-29"
    fake_ext_id = "ext_test_fabricated_fixture_99"

    async with AsyncSessionLocal() as session:
        # Insert a deliberate fake record
        session.add(HistoricalMatch(
            external_id=fake_ext_id,
            home_team="Fake United",
            away_team="Fabricated FC",
            league_id="PL",
            match_date=datetime.fromisoformat(f"{target_date} 20:00:00"),
            home_odds=1.50,
            draw_odds=4.00,
            away_odds=6.00,
            status="scheduled",
            source_classification="provider_verified",
            provider="test-fabricator"
        ))
        await session.commit()

    # Provider reports credit exhaustion
    mock_403_body = json.dumps({
        "success": False,
        "message": "Access denied. Possible reasons: Invalid key, insufficient credits, daily limit exceeded, or inactive account."
    })
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.text = AsyncMock(return_value=mock_403_body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    try:
        transport = ASGITransport(app=app)
        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.get(f"/api/calendar/daily-slate?date={target_date}")
                assert resp.status_code == 200
                data = resp.json()

                # Verify NO fake match from DB is returned!
                assert data["status"] == "error"
                assert data["matches"] == []
                assert not any(m.get("external_id") == fake_ext_id for m in data.get("matches", []))
    finally:
        # Clean up test row
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"DELETE FROM historical_matches WHERE external_id = '{fake_ext_id}'"))
            await session.commit()


@pytest.mark.asyncio
async def test_startup_purge_permanently_cleans_synthetic_rows():
    """
    Production Hardening Verification:
    Startup purge routine removes all synthetic, ext_, SYN-, and auto_ records from persistent storage.
    """
    async with AsyncSessionLocal() as session:
        # Seed test synthetic matches
        session.add(HistoricalMatch(
            external_id="ext_pl_test_fake_001",
            home_team="Team A",
            away_team="Team B",
            league_id="PL",
            match_date=datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc),
            status="scheduled",
            source_classification="seed"
        ))
        session.add(HistoricalMatch(
            external_id="SYN-TEST-999",
            home_team="Team C",
            away_team="Team D",
            league_id="PD",
            match_date=datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc),
            status="scheduled",
            source_classification="unverified"
        ))
        await session.commit()

        # Run startup purge
        await purge_synthetic_records_on_startup(session)

        # Verify rows were deleted
        res1 = await session.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == "ext_pl_test_fake_001"))
        assert res1.scalar_one_or_none() is None

        res2 = await session.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == "SYN-TEST-999"))
        assert res2.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_authentic_provider_fixtures_rendered_cleanly():
    """
    End-to-End Positive Provenance:
    When the provider successfully returns authentic fixtures with genuine IDs,
    the slate renders verified prediction cards with Poisson models and 33 prediction markets.
    """
    calendar_slate_cache.clear()
    
    genuine_matches = [
        {
            "id": "genuine_prov_fixture_101",
            "date": "2026-11-24",
            "kickoff": "20:00",
            "status": "NS",
            "home": {"id": "1", "name": "Arsenal", "score": None, "logo": "https://example.com/ars.png"},
            "away": {"id": "2", "name": "Chelsea", "score": None, "logo": "https://example.com/che.png"},
            "league": {"id": "39", "name": "Premier League", "country": "England"},
            "odds": {"home": 1.90, "draw": 3.50, "away": 4.10}
        }
    ]
    
    mock_200_body = json.dumps({
        "success": True,
        "credits_remaining": 850,
        "data": {
            "matches": genuine_matches
        }
    })
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=mock_200_body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    
    transport = ASGITransport(app=app)
    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/calendar/daily-slate?date=2026-11-24")
            assert resp.status_code == 200
            data = resp.json()
            
            assert data["status"] == "ok"
            assert data["provider_status"] == "ok"
            assert data["total_fixtures"] == 1
            assert len(data["matches"]) == 1
            
            card = data["matches"][0]
            assert card["external_id"] == "genuine_prov_fixture_101"
            assert card["home_team"] == "Arsenal"
            assert card["away_team"] == "Chelsea"
            assert card["is_synthetic"] is False
            assert "top_tip" in card
            assert card["top_prob"] > 0
            assert len(card["tips"]) > 0
            assert len(card["qualified_endpoints"]) > 0
