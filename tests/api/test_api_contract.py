import pytest
from starlette.testclient import TestClient
from main import app

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture(scope="module")
def auth_headers(client):
    res = client.get("/auth/demo-token")
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

# =====================================================================
# 1. /api/calendar/daily-slate Contract Tests
# =====================================================================
def test_calendar_daily_slate_valid_date(client):
    res = client.get("/api/calendar/daily-slate?date=2026-10-14")
    assert res.status_code == 200
    data = res.json()
    assert data["selected_date"] == "2026-10-14"
    assert "available_dates" in data
    assert "endpoints" in data
    assert "total_fixtures" in data
    assert "matches_count" in data
    assert "matches" in data
    assert isinstance(data["matches"], list)
    assert data["matches_count"] == len(data["matches"])

def test_calendar_daily_slate_next_5_days(client):
    res = client.get("/api/calendar/daily-slate?date=next-5-days")
    assert res.status_code == 200
    data = res.json()
    assert data["selected_date"] == "next-5-days"
    assert isinstance(data["matches"], list)

def test_calendar_daily_slate_market_filter(client):
    res = client.get("/api/calendar/daily-slate?date=2026-10-14&endpoint=home-win")
    assert res.status_code == 200
    data = res.json()
    assert data["active_endpoint"] == "home-win"
    for m in data["matches"]:
        assert "home-win" in m.get("qualified_endpoints", [])

def test_calendar_daily_slate_invalid_date_format(client):
    res = client.get("/api/calendar/daily-slate?date=not-a-date")
    assert res.status_code in [200, 400, 422]
    if res.status_code == 200:
        data = res.json()
        assert isinstance(data.get("matches", []), list)

# =====================================================================
# 2. /api/match-analytics Contract Tests
# =====================================================================
def test_match_analytics_valid_fixture(client):
    res = client.get("/api/match-analytics?home_team=Arsenal&away_team=Chelsea&league_id=PL&personality_mode=PROFESSIONAL")
    assert res.status_code == 200
    data = res.json()
    assert "match_info" in data
    assert "verdict" in data
    assert "audit_endpoints" in data
    assert data["match_info"]["home_team"] == "Arsenal"
    assert data["match_info"]["away_team"] == "Chelsea"

def test_match_analytics_banter_mode(client):
    res = client.get("/api/match-analytics?home_team=Arsenal&away_team=Chelsea&league_id=PL&personality_mode=AUTHENTIC_NAIJA_BANTER")
    assert res.status_code == 200
    data = res.json()
    assert "match_info" in data
    assert "verdict" in data

def test_match_analytics_missing_parameters(client):
    # Calling without required home_team and away_team should return 422 Unprocessable Entity
    res = client.get("/api/match-analytics")
    assert res.status_code == 422

# =====================================================================
# 3. /api/banker-slate Contract Tests
# =====================================================================
def test_banker_slate_thresholds(client):
    res80 = client.get("/api/banker-slate?min_prob=0.80")
    assert res80.status_code == 200
    bankers80 = res80.json()
    assert isinstance(bankers80, list)
    for b in bankers80:
        assert float(b.get("model_prob", 0)) >= 0.80

    res70 = client.get("/api/banker-slate?min_prob=0.70")
    assert res70.status_code == 200
    bankers70 = res70.json()
    assert isinstance(bankers70, list)
    assert len(bankers70) >= len(bankers80)

# =====================================================================
# 4. /api/leagues-and-teams Contract Tests
# =====================================================================
def test_leagues_and_teams(client):
    res = client.get("/api/leagues-and-teams")
    assert res.status_code == 200
    leagues = res.json()
    assert isinstance(leagues, list)
    assert len(leagues) > 0
    first = leagues[0]
    assert "id" in first
    assert "name" in first
    assert "teams" in first
    assert isinstance(first["teams"], list)

# =====================================================================
# 5. /api/upcoming-fixtures Contract Tests
# =====================================================================
def test_upcoming_fixtures(client):
    res = client.get("/api/upcoming-fixtures")
    assert res.status_code == 200
    fixtures = res.json()
    assert isinstance(fixtures, list)
