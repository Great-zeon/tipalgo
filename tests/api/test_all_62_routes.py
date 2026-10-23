import pytest
from starlette.testclient import TestClient
from main import app
import uuid
from tests.unit.test_33_markets import MARKET_TEST_CASES

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture(autouse=True)
def reset_rate_limit():
    from main import _rate_limit_store
    _rate_limit_store.clear()

@pytest.fixture(scope="module")
def auth_headers(client):
    res = client.get("/auth/demo-token")
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

# =====================================================================
# 1. System, Static, and Health Routes
# =====================================================================
def test_health_route(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert "screener_model" in data

def test_root_index_route(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<!DOCTYPE html>" in res.text or "SportyMill" in res.text or "TipAlgo" in res.text

def test_static_app_js_route(client):
    res = client.get("/static/app.js")
    assert res.status_code == 200
    assert len(res.text) > 100

def test_favicon_route(client):
    res = client.get("/favicon.ico")
    assert res.status_code in [200, 204, 404]

# =====================================================================
# 2. Authentication & User Profile Routes
# =====================================================================
def test_demo_token_route(client):
    res = client.get("/auth/demo-token")
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_auth_me_unauthorized(client):
    res = client.get("/auth/me")
    assert res.status_code == 401

def test_auth_me_authorized(client, auth_headers):
    res = client.get("/auth/me", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "id" in data
    assert "email" in data
    assert "tier" in data

def test_auth_register_and_login_lifecycle(client):
    unique_phone = f"080{uuid.uuid4().int % 100000000:08d}"
    unique_username = f"usr_{uuid.uuid4().hex[:6]}"
    unique_email = f"{unique_username}@sportymill.test"
    password = "SecurePassword123!"

    # 1. Register
    reg_payload = {
        "username": unique_username,
        "phone_number": unique_phone,
        "email": unique_email,
        "password": password,
        "confirm_password": password
    }
    reg_res = client.post("/auth/register", json=reg_payload)
    assert reg_res.status_code == 200
    reg_data = reg_res.json()
    assert "access_token" in reg_data
    assert reg_data["user"]["username"] == unique_username

    # 2. Duplicate Registration (Constraint check)
    dup_res = client.post("/auth/register", json=reg_payload)
    assert dup_res.status_code == 400

    # 3. Login
    login_res = client.post("/auth/login", json={
        "username": unique_username,
        "password": password
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    # 4. Profile update via PATCH & PUT
    patch_res = client.patch("/auth/profile", json={"personality_mode": "PROFESSIONAL"}, headers=user_headers)
    assert patch_res.status_code == 200
    assert patch_res.json()["user"]["personality_mode"] == "PROFESSIONAL"

    put_res = client.put("/auth/profile", json={"locale": "NG"}, headers=user_headers)
    assert put_res.status_code == 200
    assert put_res.json()["user"]["locale"] == "NG"

def test_auth_request_otp_and_reset_flow(client):
    # 1. Request OTP for non-existent phone
    res = client.post("/auth/request-otp", json={"phone_number": "07000000000"})
    assert res.status_code == 404

    # 2. Register a user and request valid OTP
    test_phone = f"081{uuid.uuid4().int % 100000000:08d}"
    test_user = f"otp_{uuid.uuid4().hex[:6]}"
    client.post("/auth/register", json={
        "username": test_user,
        "phone_number": test_phone,
        "password": "InitialPassword123!",
        "confirm_password": "InitialPassword123!"
    })

    otp_res = client.post("/auth/request-otp", json={"phone_number": test_phone})
    assert otp_res.status_code == 200
    otp_code = otp_res.json().get("otp_preview")

    # 3. Reset password using OTP
    reset_res = client.post("/auth/reset-password", json={
        "phone_number": test_phone,
        "otp": otp_code,
        "new_password": "NewSecretPassword456!",
        "confirm_password": "NewSecretPassword456!"
    })
    assert reset_res.status_code == 200
    assert reset_res.json().get("success") is True

# =====================================================================
# 3. Calendar, Slate, & Discovery Routes
# =====================================================================
def test_calendar_daily_slate(client):
    res = client.get("/api/calendar/daily-slate?date=2026-10-14")
    assert res.status_code == 200
    data = res.json()
    assert "matches" in data
    assert "available_dates" in data

def test_calendar_search_team(client):
    res = client.get("/api/calendar/search-team?query=Arsenal")
    assert res.status_code == 200
    data = res.json()
    assert "matches" in data
    assert "total_matches" in data

def test_calendar_league_slate(client):
    res = client.get("/api/calendar/league-slate?league_id=PL")
    assert res.status_code == 200
    data = res.json()
    assert "matches" in data
    assert data["league_id"] == "PL"

def test_dashboard_stats(client):
    res = client.get("/api/dashboard/stats")
    assert res.status_code == 200
    data = res.json()
    assert "playable_matches_today" in data
    assert "total_predictions_all_time" in data
    assert "total_leagues_tracked" in data

def test_leagues_and_teams(client):
    res = client.get("/api/leagues-and-teams")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0

def test_upcoming_fixtures(client):
    res = client.get("/api/upcoming-fixtures")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)

def test_banker_slate(client):
    res = client.get("/api/banker-slate?min_prob=0.70")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)

def test_match_analytics(client):
    res = client.get("/api/match-analytics?home_team=Arsenal&away_team=Chelsea&league_id=PL")
    assert res.status_code == 200
    data = res.json()
    assert "match_info" in data
    assert "verdict" in data
    assert "audit_endpoints" in data

# =====================================================================
# 4. Betting Slips Management Routes
# =====================================================================
def test_slips_validate_route(client):
    payload = {
        "items": [
            {"home_team": "Arsenal", "away_team": "Chelsea", "league_id": "PL", "odds": 1.85, "market": "Home Win"},
            {"home_team": "Real Madrid", "away_team": "Barcelona", "league_id": "PD", "odds": 1.60, "market": "Over 2.5"}
        ],
        "stake": 1000.0
    }
    res = client.post("/api/slips/validate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert "total_odds" in data
    assert "legs" in data

def test_slips_history(client, auth_headers):
    # Unauthenticated works as guest
    res_guest = client.get("/api/slips/history")
    assert res_guest.status_code == 200
    assert "slips" in res_guest.json()

    # Authenticated returns user history
    res_auth = client.get("/api/slips/history", headers=auth_headers)
    assert res_auth.status_code == 200
    assert "slips" in res_auth.json()

def test_slips_save(client, auth_headers):
    payload = {
        "items": [
            {"home_team": "Arsenal", "away_team": "Chelsea", "league_id": "PL", "odds": 1.85, "market": "Home Win"}
        ],
        "total_odds": 1.85,
        "stake": 1000.0,
        "potential_payout": 1850.0
    }
    res = client.post("/api/slips/save", json=payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "booking_code" in data
    assert "slip_id" in data

# =====================================================================
# 5. Payments & Webhooks Routes
# =====================================================================
def test_payments_initialize_unauthorized(client):
    res = client.post("/payments/initialize", json={})
    assert res.status_code == 401

def test_payments_initialize_authorized(client, auth_headers):
    res = client.post("/payments/initialize", json={}, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "reference" in data

def test_payments_verify_unauthorized(client):
    res = client.post("/payments/verify/invalid-reference-12345")
    assert res.status_code == 401

def test_payments_verify_authorized(client, auth_headers):
    res = client.post("/payments/verify/mock-ref-audit", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["tier"] == "pro"

def test_paystack_webhook_invalid_signature(client):
    res = client.post(
        "/webhook/paystack",
        json={"event": "charge.success", "data": {"reference": "test_ref"}},
        headers={"x-paystack-signature": "forged_invalid_signature"}
    )
    assert res.status_code == 400

# =====================================================================
# 6. Admin & Sync Operations Routes
# =====================================================================
def test_sync_live_data_unauthorized(client):
    res = client.post("/api/sync-live-data")
    assert res.status_code == 401

def test_admin_settle_predictions_unauthorized(client):
    res = client.post("/admin/settle-predictions")
    assert res.status_code in [401, 403]

def test_admin_train_unauthorized(client):
    res = client.post("/admin/train")
    assert res.status_code in [401, 403]

def test_admin_upload_database_unauthorized(client):
    res = client.post("/admin/upload-database")
    assert res.status_code in [401, 403, 422]

# =====================================================================
# 7. Complete Systematic Audit of All 33 Prediction Routes
# =====================================================================
@pytest.mark.parametrize("market_id, payload", MARKET_TEST_CASES)
def test_all_33_prediction_endpoints_validation_and_math(client, auth_headers, market_id, payload):
    url = f"/predict/{market_id}"

    # 1. Unauthenticated request -> 401 Unauthorized
    unauth_res = client.post(url, json=payload)
    assert unauth_res.status_code == 401, f"Endpoint {market_id} did not guard authentication"

    # 2. Authenticated empty body schema validation -> 422 Unprocessable Entity
    empty_res = client.post(url, json={}, headers=auth_headers)
    assert empty_res.status_code == 422, f"Endpoint {market_id} did not enforce required schema"

    # 3. Authenticated valid fixture execution -> 200 & verified response structure
    res = client.post(url, json=payload, headers=auth_headers)
    assert res.status_code == 200, f"Endpoint {market_id} failed with {res.status_code}: {res.text}"
    data = res.json()
    assert isinstance(data, dict), f"Endpoint {market_id} returned invalid type"
    assert "verdict" in data or "strategy" in data or "endpoint" in data or "legs" in data or "matches" in data or "results" in data
