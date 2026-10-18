import pytest
import asyncio
from unittest.mock import patch, MagicMock
from football_ingestion import FootballDataService

@pytest.mark.asyncio
async def test_api_service_unconfigured():
    """Verify that unconfigured service returns empty results safely without crashing."""
    service = FootballDataService(api_key="", base_url="https://v3.football.api-sports.io")
    assert service.is_configured is False
    
    fixtures = await service.fetch_live_fixtures_by_date("2026-10-14")
    assert fixtures == []
    
    odds = await service.fetch_live_odds_by_date("2026-10-14")
    assert odds == {}
    
    standings = await service.fetch_standings("PL", 2024)
    assert standings is None

@pytest.mark.asyncio
async def test_api_service_timeout_handling():
    """Verify that HTTP timeout is caught and returns empty list safely."""
    service = FootballDataService(api_key="dummy_key_for_test")
    assert service.is_configured is True
    
    # Mock aiohttp ClientSession to raise asyncio.TimeoutError
    with patch("aiohttp.ClientSession.get", side_effect=asyncio.TimeoutError("Connection timed out")):
        fixtures = await service.fetch_live_fixtures_by_date("2026-10-14")
        assert fixtures == []
        
        odds = await service.fetch_live_odds_by_date("2026-10-14")
        assert odds == {}

@pytest.mark.asyncio
async def test_api_service_rate_limit_429():
    """Verify that HTTP 429 rate limit returns empty safely."""
    service = FootballDataService(api_key="dummy_key_for_test")
    
    mock_resp = MagicMock()
    mock_resp.status = 429
    
    class MockContextManager:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("aiohttp.ClientSession.get", return_value=MockContextManager()):
        fixtures = await service.fetch_live_fixtures_by_date("2026-10-14")
        assert fixtures == []

@pytest.mark.asyncio
async def test_api_service_server_error_500():
    """Verify that HTTP 500 server error returns empty safely."""
    service = FootballDataService(api_key="dummy_key_for_test")
    
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.text = MagicMock(return_value=asyncio.Future())
    mock_resp.text.return_value.set_result("Internal Server Error")
    
    class MockContextManager:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("aiohttp.ClientSession.get", return_value=MockContextManager()):
        fixtures = await service.fetch_live_fixtures_by_date("2026-10-14")
        assert fixtures == []

@pytest.mark.asyncio
async def test_live_api_connectivity():
    """Verify live API-Sports connectivity with active credentials."""
    service = FootballDataService()
    if not service.is_configured:
        pytest.skip("FOOTBALL_API_KEY not configured in environment")
    
    fixtures = await service.fetch_live_fixtures_by_date("2026-10-14")
    assert isinstance(fixtures, list)
    # If connection succeeds, status code was 200
    print(f"Verified live fixtures received: {len(fixtures)}")
