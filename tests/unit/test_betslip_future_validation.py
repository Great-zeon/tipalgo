import pytest
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient
from main import app, config, AsyncSessionLocal, HistoricalMatch

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

@pytest.mark.asyncio
async def test_betslip_future_fixture_acceptance(client):
    """
    Bet Slip must accept upcoming matches scheduled for future dates
    (tomorrow, next week, etc.) when lifecycle is 'scheduled' / 'upcoming'.
    """
    now = datetime.now(timezone.utc)
    future_date = (now + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    future_day_str = (now + timedelta(days=5)).strftime("%Y-%m-%d")

    # Seed a future fixture in database
    async with AsyncSessionLocal() as session:
        match = HistoricalMatch(
            external_id="future_fixture_9999",
            home_team="Future Home FC",
            away_team="Future Away FC",
            league_id="PL",
            match_date=datetime.fromisoformat(future_date),
            status="scheduled",
            provider_status="NS",
            home_score=None,
            away_score=None
        )
        session.add(match)
        await session.commit()

    # Validate bet slip containing future fixture
    slip_payload = {
        "stake": 5000.0,
        "items": [
            {
                "fixture_id": "future_fixture_9999",
                "match": "Future Home FC vs Future Away FC",
                "home_team": "Future Home FC",
                "away_team": "Future Away FC",
                "league": "Premier League",
                "league_id": "PL",
                "market": "Home Win",
                "tip": "Home Win",
                "odds": 1.95,
                "status": "scheduled",
                "match_date": future_date
            }
        ]
    }

    res = client.post("/api/slips/validate", json=slip_payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["valid"] is True, f"Future fixture was rejected: {data.get('error')}"
    assert data["legs_count"] == 1
    assert data["total_odds"] == 1.95

    # Cleanup seeded fixture
    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete
        await session.execute(delete(HistoricalMatch).where(HistoricalMatch.external_id == "future_fixture_9999"))
        await session.commit()

@pytest.mark.asyncio
async def test_betslip_finished_fixture_rejection(client):
    """Bet Slip must reject completed fixtures with authoritative scores."""
    now = datetime.now(timezone.utc)
    past_date = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    async with AsyncSessionLocal() as session:
        match = HistoricalMatch(
            external_id="past_fixture_8888",
            home_team="Finished Home FC",
            away_team="Finished Away FC",
            league_id="PL",
            match_date=datetime.fromisoformat(past_date),
            status="finished",
            provider_status="FT",
            home_score=3,
            away_score=1
        )
        session.add(match)
        await session.commit()

    slip_payload = {
        "stake": 1000.0,
        "items": [
            {
                "fixture_id": "past_fixture_8888",
                "match": "Finished Home FC vs Finished Away FC",
                "home_team": "Finished Home FC",
                "away_team": "Finished Away FC",
                "league": "Premier League",
                "league_id": "PL",
                "market": "Home Win",
                "odds": 1.50,
                "status": "finished",
                "match_date": past_date
            }
        ]
    }

    res = client.post("/api/slips/validate", json=slip_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is False
    assert "already finished" in data["error"]

    # Cleanup
    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete
        await session.execute(delete(HistoricalMatch).where(HistoricalMatch.external_id == "past_fixture_8888"))
        await session.commit()

@pytest.mark.asyncio
async def test_betslip_live_fixture_rejection(client):
    """Bet slip must reject live in-play matches."""
    slip_payload = {
        "stake": 1000.0,
        "items": [
            {
                "match": "Live Team A vs Live Team B",
                "home_team": "Live Team A",
                "away_team": "Live Team B",
                "league_id": "PL",
                "market": "Over 2.5",
                "odds": 1.80,
                "status": "live"
            }
        ]
    }
    res = client.post("/api/slips/validate", json=slip_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is False
    assert "currently live" in data["error"]

def test_betslip_invalid_odds_and_duplicate_rejection(client):
    """Bet slip must reject invalid odds (<= 1.00) and duplicate selections."""
    # Odds <= 1.00
    res_odds = client.post("/api/slips/validate", json={
        "stake": 1000.0,
        "items": [{
            "match": "Team A vs Team B",
            "home_team": "Team A",
            "away_team": "Team B",
            "league_id": "PL",
            "market": "Home Win",
            "odds": 0.95,
            "status": "scheduled"
        }]
    })
    assert res_odds.status_code == 200
    assert res_odds.json()["valid"] is False
    assert "strictly greater than 1.00" in res_odds.json()["error"]

    # Duplicate match in slip
    res_dup = client.post("/api/slips/validate", json={
        "stake": 1000.0,
        "items": [
            {
                "match": "Team A vs Team B",
                "home_team": "Team A",
                "away_team": "Team B",
                "league_id": "PL",
                "market": "Home Win",
                "odds": 1.80,
                "status": "scheduled"
            },
            {
                "match": "Team A vs Team B",
                "home_team": "Team A",
                "away_team": "Team B",
                "league_id": "PL",
                "market": "Over 2.5",
                "odds": 1.90,
                "status": "scheduled"
            }
        ]
    })
    assert res_dup.status_code == 200
    assert res_dup.json()["valid"] is False
    assert "Duplicate selection detected" in res_dup.json()["error"]
