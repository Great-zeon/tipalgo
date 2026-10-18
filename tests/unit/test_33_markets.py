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

MATCH_BASE = {
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "league_id": "PL",
    "bankroll": 50000.0,
    "personality_mode": "PROFESSIONAL",
    "locale": "NG"
}

MARKET_TEST_CASES = [
    ("home-win", {**MATCH_BASE, "home_odds": 1.65}),
    ("straight-home-win", {**MATCH_BASE, "home_odds": 1.65, "away_odds": 5.20}),
    ("strong-home-dc", {**MATCH_BASE, "home_odds": 1.40, "draw_odds": 4.50, "away_odds": 7.50}),
    ("double-chance", {**MATCH_BASE, "home_odds": 1.65, "draw_odds": 3.80, "away_odds": 5.20, "live_xg": 2.1}),
    ("away-win", {**MATCH_BASE, "away_odds": 5.20}),
    ("draw", {**MATCH_BASE, "draw_odds": 3.80}),
    ("over-1-5", {**MATCH_BASE, "over_odds": 1.25}),
    ("over-2-5", {**MATCH_BASE, "over_odds": 1.75}),
    ("over-3-5", {**MATCH_BASE, "over_odds": 2.80}),
    ("btts-yes", {**MATCH_BASE, "btts_odds": 1.70}),
    ("btts-no", {**MATCH_BASE, "btts_no_odds": 2.15}),
    ("team-total-goals", {**MATCH_BASE, "target_team": "Arsenal", "venue": "home", "line": 1.5, "over_odds": 1.65, "under_odds": 2.20}),
    ("no-three-goals", {**MATCH_BASE, "home_odds": 2.10, "away_odds": 2.20, "no_three_odds": 1.45}),
    ("btts-over-2-5", {**MATCH_BASE, "odds": 2.05}),
    ("goal-both-halves", {**MATCH_BASE, "odds": 1.65}),
    ("ht-result", {**MATCH_BASE, "h_odds": 2.10, "d_odds": 2.30, "a_odds": 5.50}),
    ("win-to-nil", {**MATCH_BASE, "odds": 2.80}),
    ("correct-score", MATCH_BASE),
    ("team-to-score", {**MATCH_BASE, "home_odds": 1.65, "away_odds": 5.20}),
    ("corners-total", {**MATCH_BASE, "line": 9.5, "over_odds": 1.85, "under_odds": 1.95}),
    ("corners-1x2", MATCH_BASE),
    ("cards-total", {**MATCH_BASE, "line": 3.5, "over_odds": 1.80, "under_odds": 2.00}),
    ("cards-1x2", {**MATCH_BASE, "home_odds": 2.20, "draw_odds": 3.40, "away_odds": 2.60}),
    ("ml-predictor", {**MATCH_BASE, "home_odds": 1.65, "draw_odds": 3.80, "away_odds": 5.20}),
    ("sure-bet", {**MATCH_BASE, "market_odds": {"home_win": 1.65, "draw": 3.80, "away_win": 5.20}, "min_edge": 0.02}),
    ("value-screener", {**MATCH_BASE, "home_odds": 1.65, "draw_odds": 3.80, "away_odds": 5.20, "min_edge": 0.02}),
    ("super-tips", {**MATCH_BASE, "home_odds": 1.65, "draw_odds": 3.80, "away_odds": 5.20}),
    ("form-gap", {**MATCH_BASE, "home_odds": 1.60, "away_odds": 5.50}),
    ("batch-scan", {
        "matches": [
            {"home_team": "Arsenal", "away_team": "Chelsea", "league_id": "PL", "market_odds": {"home_win": 1.65, "draw": 3.80, "away_win": 5.20}}
        ],
        "min_edge": 0.01
    }),
    ("away-underdog", {**MATCH_BASE, "away_odds": 5.50}),
    ("fade-underdog", {**MATCH_BASE, "home_odds": 1.60, "away_odds": 5.50}),
    ("underdog-trap", {**MATCH_BASE, "home_odds": 1.60, "away_odds": 5.50}),
    ("accumulator", {
        "target_odds": 4.0,
        "max_legs": 3,
        "min_probability": 0.50,
        "min_edge": 0.01,
        "randomise": False,
        "tolerance": 0.3
    }),
]

@pytest.mark.parametrize("market_id, payload", MARKET_TEST_CASES)
def test_all_33_markets_contract(client, auth_headers, market_id, payload):
    """Verify that every single one of the 33 prediction endpoints returns valid HTTP 200 and schema."""
    url = f"/predict/{market_id}"
    resp = client.post(url, json=payload, headers=auth_headers)
    assert resp.status_code == 200, f"Market {market_id} failed with status {resp.status_code}: {resp.text}"
    
    data = resp.json()
    assert isinstance(data, dict), f"Market {market_id} did not return JSON object: {data}"
    
    # Must contain pattern_matched or selection or results or overall_probability
    has_valid_key = any(k in data for k in ["pattern_matched", "selection", "results", "overall_probability", "top_pick", "feature"])
    assert has_valid_key, f"Market {market_id} missing standard prediction response key: {list(data.keys())}"
    
    # Verify probability bounds if probability is present
    if "probability" in data and data["probability"] is not None:
        p = float(data["probability"])
        # If represented as percentage (0-100) or decimal (0-1)
        assert 0.0 <= p <= 100.0, f"Market {market_id} returned probability outside [0, 100]: {p}"

def test_missing_team_insufficient_data(client, auth_headers):
    """Verify that a non-existent team raises proper error and does not fabricate data."""
    payload = {
        "home_team": "NonExistentTeamAlpha",
        "away_team": "NonExistentTeamBeta",
        "league_id": "PL",
        "home_odds": 1.50
    }
    resp = client.post("/predict/home-win", json=payload, headers=auth_headers)
    # The system should either return 400/422/500 or pattern_matched: False with explicit explanation
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("pattern_matched") is False or "Insufficient" in data.get("message", "") or "Insufficient" in str(data.get("explanation", ""))
    else:
        assert resp.status_code in [400, 422, 500]

def test_invalid_market_id_returns_404(client, auth_headers):
    """Verify that unknown prediction endpoints return 404."""
    resp = client.post("/predict/non-existent-market", json=MATCH_BASE, headers=auth_headers)
    assert resp.status_code == 404
