"""
TipAlgo v21 - Automated Production Regression Test Suite
Validates REGRESSION-001 through REGRESSION-015 as mandated by production readiness gate.
"""
import unittest
import asyncio
import os
import re
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import select, delete

from main import (
    app, config, AsyncSessionLocal, User, LeagueTable, HistoricalMatch,
    PredictorService, ResponseBuilder, InsufficientDataError,
    generate_correct_score_tip, get_verified_squad_meta, create_access_token
)
from football_ingestion import FootballDataService

BASE_URL = "http://testserver"

class TestRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url=BASE_URL, timeout=30.0)
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(User).where(User.email == "demo@tipalgo.com"))
            user = res.scalar_one_or_none()
            if not user:
                user = User(
                    email="demo@tipalgo.com",
                    username="demopro",
                    password_hash="mock",
                    subscription_tier="pro",
                    subscription_expiry=datetime.now(timezone.utc) + timedelta(days=365)
                )
                db.add(user)
                await db.commit()
            self.token = create_access_token({"sub": user.id, "email": user.email})
            self.auth_headers = {"Authorization": f"Bearer {self.token}"}

    async def asyncTearDown(self):
        await self.client.aclose()

    # REGRESSION-001: Unknown/unseeded team cannot receive synthetic statistics
    async def test_regression_001_unknown_team_safe_failure(self):
        async with AsyncSessionLocal() as db:
            predictor = PredictorService()
            with self.assertRaises(InsufficientDataError):
                await predictor.get_probabilities(db, "Atlantis FC", "El Dorado Wanderers", "PL")

        # Test via HTTP endpoint
        resp = await self.client.post("/predict/home-win", json={
            "home_team": "Atlantis FC",
            "away_team": "El Dorado Wanderers",
            "league_id": "PL",
            "home_odds": 2.10
        }, headers=self.auth_headers)
        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertFalse(data.get("pattern_matched"))
        self.assertEqual(data.get("status"), "insufficient_data")
        self.assertIn("insufficient", data.get("message", "").lower())

    # REGRESSION-002: Different teams cannot receive identical fallback statistics (60.2%, 12.6%, 2.1)
    async def test_regression_002_no_identical_mock_fallbacks(self):
        resp1 = await self.client.post("/predict/home-win", json={
            "home_team": "Unknown Team Alpha",
            "away_team": "Unknown Team Beta",
            "league_id": "PL",
            "home_odds": 2.10
        }, headers=self.auth_headers)
        self.assertEqual(resp1.status_code, 422)

        resp2 = await self.client.post("/predict/home-win", json={
            "home_team": "Unknown Team Gamma",
            "away_team": "Unknown Team Delta",
            "league_id": "PL",
            "home_odds": 2.10
        }, headers=self.auth_headers)
        self.assertEqual(resp2.status_code, 422)

        # Neither team received synthetic 60.2% probability or 12.6% edge
        data1 = resp1.json()
        data2 = resp2.json()
        self.assertNotEqual(data1.get("probability"), 60.2)
        self.assertNotEqual(data2.get("probability"), 60.2)

    # REGRESSION-003: Frontend cannot display fake prediction when pattern_matched=false
    async def test_regression_003_frontend_pass_handling(self):
        with open("static/app.js", "r") as f:
            app_js = f.read()

        # Verify hardcoded 66.4% and 36,450 fallback values are eliminated from updateActiveFixtureUI
        self.assertNotIn("const prob = res.probability || 66.4;", app_js)
        self.assertNotIn("res.edge || 5.7", app_js)
        self.assertNotIn("res.stake_suggestion || '₦36,450'", app_js)
        self.assertIn("res.pattern_matched === false || res.status === 'insufficient_data'", app_js)
        self.assertIn("Model Pass — No Qualifying Value", app_js)

    # REGRESSION-004: Negative edge is not multiplied by 100 twice
    async def test_regression_004_edge_scaling(self):
        # 1. Decimal fraction negative edge: -0.161 -> -16.1%
        res1 = ResponseBuilder.success("test", "A vs B", "A Win", 50.0, -0.161, 2.0, 0, "LOW", {})
        self.assertEqual(res1["edge"], -16.1)

        # 2. Pre-scaled percentage negative edge: -16.1 -> -16.1%
        res2 = ResponseBuilder.success("test", "A vs B", "A Win", 50.0, -16.1, 2.0, 0, "LOW", {})
        self.assertEqual(res2["edge"], -16.1)

        # 3. Decimal fraction positive edge: 0.126 -> 12.6%
        res3 = ResponseBuilder.success("test", "A vs B", "A Win", 60.0, 0.126, 2.0, 1000, "HIGH", {})
        self.assertEqual(res3["edge"], 12.6)

        # 4. Pre-scaled positive edge: 12.6 -> 12.6%
        res4 = ResponseBuilder.success("test", "A vs B", "A Win", 60.0, 12.6, 2.0, 1000, "HIGH", {})
        self.assertEqual(res4["edge"], 12.6)

        # 5. Zero edge: 0.0 -> 0.0%
        res5 = ResponseBuilder.success("test", "A vs B", "A Win", 50.0, 0.0, 2.0, 0, "LOW", {})
        self.assertEqual(res5["edge"], 0.0)

    # REGRESSION-005: Correct-score top-level probability matches candidate probability
    async def test_regression_005_correct_score_probability_parity(self):
        async with AsyncSessionLocal() as db:
            predictor = PredictorService()
            # Predict between known seeded teams Arsenal vs Chelsea
            tip = await generate_correct_score_tip(db, predictor, "Arsenal", "Chelsea", "PL", 100000.0)
            self.assertTrue(tip.get("pattern_matched"))
            top_prob = tip.get("probability")
            candidates = tip.get("candidates", [])
            self.assertTrue(len(candidates) > 0)
            candidate_prob_raw = candidates[0].get("probability")
            candidate_prob_num = float(str(candidate_prob_raw).replace("%", ""))
            # Top-level probability must match candidate probability on percentage scale (not 0.1 vs 9.1%)
            self.assertAlmostEqual(top_prob, candidate_prob_num, places=1)

    # REGRESSION-006: Frontend market odds cannot be hardcoded
    async def test_regression_006_no_hardcoded_market_odds_in_app_js(self):
        with open("static/app.js", "r") as f:
            app_js = f.read()

        # Verify hardcoded over_odds and prop odds were purged from buildFixturePayload
        self.assertNotIn("over_odds: 1.25", app_js)
        self.assertNotIn("over_odds: 1.75", app_js)
        self.assertNotIn("over_odds: 2.80", app_js)
        self.assertNotIn("btts_odds: 1.70", app_js)
        self.assertNotIn("btts_no_odds: 2.15", app_js)
        self.assertIn("f.over_2_5_odds || f.market_odds?.over_2_5", app_js)

    # REGRESSION-007: Fabricated players cannot appear in production analytics
    async def test_regression_007_no_fabricated_players(self):
        meta = get_verified_squad_meta("Arsenal", is_home=True)
        self.assertFalse(meta.get("lineup_available"))
        self.assertEqual(len(meta.get("key_threats", [])), 0)
        self.assertIn("unavailable", meta.get("message", "").lower())

        # Check full match analytics endpoint
        resp = await self.client.get("/api/match-analytics?home_team=Arsenal&away_team=Chelsea&league_id=PL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        raw_str = str(data)
        self.assertNotIn("Top Striker", raw_str)
        self.assertNotIn("GK", raw_str)
        self.assertNotIn("Winger", raw_str)

    # REGRESSION-008: Fabricated odds cannot appear
    async def test_regression_008_no_fabricated_odds_fallbacks(self):
        service = FootballDataService(api_key="")
        odds = await service.fetch_fixture_odds(99999999)
        # Without valid external provider response, odds must be None, NOT fake 2.0 / 3.2 / 3.5
        self.assertIsNone(odds.get("home_odds"))
        self.assertIsNone(odds.get("draw_odds"))
        self.assertIsNone(odds.get("away_odds"))

    # REGRESSION-009: Analytics narrative follows actual statistical advantage
    async def test_regression_009_narrative_follows_numbers(self):
        # Match where Away team (Arsenal) is vastly superior to Home team (Sheffield United)
        resp = await self.client.get("/api/match-analytics?home_team=Sheffield United&away_team=Arsenal&league_id=PL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        narrative = data.get("verdict", {}).get("narrative", "")
        # Narrative MUST NOT state that Sheffield United has statistical superiority
        self.assertNotIn("Statistical Superiority of Sheffield United", narrative)
        # Narrative must recognize Arsenal's advantage
        self.assertIn("Arsenal", narrative)

    # REGRESSION-010: Completed matches cannot appear as upcoming fixtures
    async def test_regression_010_no_finished_matches_in_upcoming(self):
        resp = await self.client.get("/api/upcoming-fixtures")
        self.assertEqual(resp.status_code, 200)
        fixtures = resp.json()
        now = datetime.now(timezone.utc)
        for f in fixtures:
            # Match date must be in future or right at current time
            m_date = datetime.fromisoformat(f["match_date"].replace("Z", "+00:00"))
            if m_date.tzinfo is None:
                m_date = m_date.replace(tzinfo=timezone.utc)
            self.assertGreaterEqual(m_date, now - timedelta(minutes=5))
            # No synthetic FUT- test fixtures in production feed
            self.assertFalse(f["external_id"].startswith("FUT-"))

    # REGRESSION-011: Swagger is disabled/protected in production
    async def test_regression_011_swagger_disabled_in_prod(self):
        # In .env DEBUG=False
        resp_docs = await self.client.get("/docs")
        self.assertEqual(resp_docs.status_code, 404)

        resp_openapi = await self.client.get("/openapi.json")
        self.assertEqual(resp_openapi.status_code, 404)

    # REGRESSION-012: CORS does not allow unauthorized wildcard origins
    async def test_regression_012_cors_restrictions(self):
        resp = await self.client.options("/api/upcoming-fixtures", headers={
            "Origin": "http://malicious-phishing-site.com",
            "Access-Control-Request-Method": "GET"
        })
        # Wildcard * with credentials must NOT be echoed for unauthorized origin
        allow_origin = resp.headers.get("access-control-allow-origin")
        self.assertNotEqual(allow_origin, "*")
        self.assertNotEqual(allow_origin, "http://malicious-phishing-site.com")

    # REGRESSION-013: Missing Football API data produces safe failure
    async def test_regression_013_missing_data_safe_failure(self):
        resp = await self.client.post("/predict/double-chance", json={
            "home_team": "NonExistent FC",
            "away_team": "Ghost United",
            "league_id": "PL",
            "home_odds": 1.5,
            "draw_odds": 3.5,
            "away_odds": 6.0
        }, headers=self.auth_headers)
        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertFalse(data.get("pattern_matched"))
        self.assertEqual(data.get("status"), "insufficient_data")

    # REGRESSION-014: Stale data cannot be represented as live data
    async def test_regression_014_stale_data_guard(self):
        async with AsyncSessionLocal() as db:
            # Ensure any leftover from previous runs is purged first
            await db.execute(delete(HistoricalMatch).where(HistoricalMatch.external_id == "STALE-001"))
            await db.commit()
            # Insert a match from 10 days ago marked as 'scheduled'
            old_match = HistoricalMatch(
                external_id="STALE-001",
                home_team="Stale FC",
                away_team="Ancient United",
                league_id="PL",
                match_date=datetime.now(timezone.utc) - timedelta(days=10),
                home_odds=2.0,
                draw_odds=3.2,
                away_odds=3.5,
                status="scheduled"
            )
            db.add(old_match)
            await db.commit()

        try:
            # Query upcoming fixtures: must NOT return the 10-day-old match
            resp = await self.client.get("/api/upcoming-fixtures")
            self.assertEqual(resp.status_code, 200)
            external_ids = [f["external_id"] for f in resp.json()]
            self.assertNotIn("STALE-001", external_ids)
        finally:
            # Cleanup
            async with AsyncSessionLocal() as db:
                await db.execute(delete(HistoricalMatch).where(HistoricalMatch.external_id == "STALE-001"))
                await db.commit()

    # REGRESSION-015: Concurrent requests remain isolated
    async def test_regression_015_concurrency_isolation(self):
        async def make_pred(home, away, odds):
            return await self.client.post("/predict/home-win", json={
                "home_team": home,
                "away_team": away,
                "league_id": "PL",
                "home_odds": odds
            }, headers=self.auth_headers)

        tasks = [
            make_pred("Arsenal", "Chelsea", 1.65),
            make_pred("Liverpool", "Tottenham", 1.55),
            make_pred("Manchester City", "Aston Villa", 1.30),
            make_pred("Newcastle", "Brighton", 1.75),
        ]
        results = await asyncio.gather(*tasks)
        for r in results:
            self.assertEqual(r.status_code, 200)
            data = r.json()
            # Match title in response must strictly correspond to requested match
            match_str = data.get("match", "")
            if "Arsenal" in match_str:
                self.assertIn("Chelsea", match_str)
                self.assertNotIn("Liverpool", match_str)
            elif "Liverpool" in match_str:
                self.assertIn("Tottenham", match_str)
                self.assertNotIn("Arsenal", match_str)
            elif "Manchester City" in match_str:
                self.assertIn("Aston Villa", match_str)
                self.assertNotIn("Newcastle", match_str)

    # REGRESSION-016: Elimination of hardcoded mock values (₦24,300, 64.2%, 10.1%, 23.4%) from live UI fallbacks
    async def test_regression_016_no_hardcoded_live_prediction_fallbacks(self):
        with open("static/index.html", "r") as f:
            html = f.read()
        with open("static/app.js", "r") as f:
            js = f.read()

        # index.html must not contain the mock result card values
        self.assertNotIn("₦24,300", html)
        self.assertNotIn("Arsenal Win (Value)", html)
        self.assertNotIn("resProbVal\">64.2%", html)
        self.assertNotIn("resEdgeVal\">+10.1%", html)
        self.assertNotIn("accuKpiAchieved\">5.12", html)
        self.assertNotIn("accuKpiProb\">23.4%", html)

        # app.js must not contain fallback 24300 or mock numbers in live renderers
        self.assertNotIn("|| 24300", js)
        self.assertNotIn("|| 64.2", js)
        self.assertNotIn("|| 10.1", js)
        self.assertNotIn("hp = 64.2", js)
        self.assertNotIn("Target: 5.00 | Achieved: 5.12 | Prob: 23.4%", js)

    # REGRESSION-017: Independent Mathematical Verification of Kelly and Edge Calculations
    async def test_regression_017_kelly_and_edge_mathematics(self):
        from main import _calculate_kelly_stake

        # Test Case A: Valid positive edge
        # Model prob: 0.60, Odds: 2.00 (implied 0.50, edge +0.10, EV = 0.20)
        # Full Kelly: (0.60*1.0 - 0.40) / 1.0 = 0.20
        # Quarter Kelly: 0.20 * 0.25 = 0.05 (5%)
        # Cap: min(0.05, 0.08) = 0.05
        # On ₦1,000,000 bankroll -> ₦50,000.00
        stake_a = _calculate_kelly_stake(0.60, 2.00, 1000000.0)
        self.assertEqual(stake_a, 50000.00)

        # Test Case B: Edge capped at 8% allocation
        # Model prob: 0.85, Odds: 2.00 (Full Kelly = 0.70, Quarter Kelly = 0.175)
        # Final allocation capped at 0.08
        # On ₦1,000,000 bankroll -> ₦80,000.00
        stake_b = _calculate_kelly_stake(0.85, 2.00, 1000000.0)
        self.assertEqual(stake_b, 80000.00)

        # Test Case C: Zero edge (prob == implied)
        # Model prob: 0.50, Odds: 2.00 (edge 0.0) -> stake must be 0
        stake_c = _calculate_kelly_stake(0.50, 2.00, 1000000.0)
        self.assertEqual(stake_c, 0.0)

        # Test Case D: Negative edge (prob < implied)
        # Model prob: 0.40, Odds: 2.00 (edge -0.10) -> stake must be 0
        stake_d = _calculate_kelly_stake(0.40, 2.00, 1000000.0)
        self.assertEqual(stake_d, 0.0)

        # Test Case E: Invalid odds (<= 1.0)
        self.assertEqual(_calculate_kelly_stake(0.60, 1.00, 1000000.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(0.60, 0.90, 1000000.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(0.60, -1.50, 1000000.0), 0.0)

        # Test Case F: Negative or zero bankroll
        self.assertEqual(_calculate_kelly_stake(0.60, 2.00, 0.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(0.60, 2.00, -50000.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(0.60, 2.00, None), 0.0)

        # Test Case G: Invalid probabilities outside (0, 1]
        self.assertEqual(_calculate_kelly_stake(0.0, 2.00, 1000000.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(-0.10, 2.00, 1000000.0), 0.0)
        self.assertEqual(_calculate_kelly_stake(1.50, 2.00, 1000000.0), 0.0)

    # REGRESSION-018: Arsenal vs Brentford live inputs recalculation consistency
    async def test_regression_018_arsenal_brentford_calculation_consistency(self):
        from main import _calculate_kelly_stake

        # Reported case: Arsenal odds 1.48, Model prob 64.2%
        odds = 1.48
        model_prob = 0.642
        implied_prob = 1.0 / odds # 0.675675... (67.57%)
        edge = model_prob - implied_prob # -0.033675... (-3.37%)
        self.assertLess(edge, 0.0, "Arsenal vs Brentford has negative edge on 1.48 odds")

        # Because edge is negative, Kelly stake MUST be ₦0
        stake = _calculate_kelly_stake(model_prob, odds, 1000000.0)
        self.assertEqual(stake, 0.0, "Negative edge must never yield a positive Kelly stake")

        # Now test educational case: Arsenal odds 1.85, Model prob 64.2%
        odds_hypo = 1.85
        implied_hypo = 1.0 / odds_hypo # 0.54054... (54.05%)
        edge_hypo = model_prob - implied_hypo # +0.10145... (+10.15%)
        self.assertAlmostEqual(edge_hypo, 0.1015, places=3)
        b = odds_hypo - 1.0 # 0.85
        full_kelly = (model_prob * b - (1.0 - model_prob)) / b # 0.2208235...
        quarter_kelly = 0.25 * full_kelly # 0.0552058... (~5.52%)
        stake_hypo = _calculate_kelly_stake(model_prob, odds_hypo, 1000000.0)
        self.assertEqual(stake_hypo, 55205.88)
        # It is definitively ₦55,206 (5.52%), NOT the previously reported mockup value ₦24,300 (2.43%)
        self.assertNotEqual(stake_hypo, 24300.0)

    # REGRESSION-019: Value Screener Minimum Edge Floor and Zero Stake on Failure
    async def test_regression_019_value_screener_edge_floor_and_zero_stake(self):
        resp = await self.client.post("/predict/value-screener", json={
            "home_team": "Arsenal",
            "away_team": "Brentford",
            "league_id": "PL",
            "home_odds": 1.48,
            "draw_odds": 4.50,
            "away_odds": 6.50,
            "bankroll": 1000000.0,
            "min_edge": 0.04
        }, headers=self.auth_headers)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        if data.get("pattern_matched") is False:
            self.assertFalse(data.get("pattern_matched"))
            # If pattern matched is false, stake cannot be positive
            self.assertEqual(data.get("stake", 0), 0)
        else:
            # If qualified, edge must strictly exceed or meet the 4% threshold
            self.assertGreaterEqual(data["edge"], 4.0)
            self.assertGreater(data["stake"], 0)

    # REGRESSION-020: Accumulator Correlated Legs Rejection (DEF-001)
    async def test_regression_020_accumulator_no_duplicate_matches(self):
        resp = await self.client.post("/predict/accumulator", json={
            "target_odds": 5.0,
            "max_legs": 4,
            "min_probability": 0.40,
            "min_edge": 0.01,
            "tolerance": 0.25
        }, headers=self.auth_headers)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        legs = data.get("legs", [])
        if legs:
            matches = [leg["match"] for leg in legs]
            unique_matches = set(matches)
            self.assertEqual(len(matches), len(unique_matches), "Accumulator ticket must not contain duplicate matches")

    # REGRESSION-021: Negative Edge (-3.37%) Is Preserved and Not Silently Replaced With 0.0%
    async def test_regression_021_value_screener_negative_edge_not_silently_zeroed(self):
        # 1. Exact mathematical formula check for 64.2% prob at 1.48 odds
        model_prob = 0.642
        decimal_odds = 1.48
        implied_prob = 1.0 / decimal_odds # 0.675675675...
        calculated_edge = model_prob - implied_prob # -0.033675675... (-3.37%)
        
        # Format to percentage rounded to 2 decimal places
        formatted_edge_pct = round(calculated_edge * 100, 2)
        self.assertEqual(formatted_edge_pct, -3.37, "64.2% probability at 1.48 odds must yield exactly -3.37% edge")
        self.assertNotEqual(formatted_edge_pct, 0.0, "Negative edge must never be silently zeroed to 0.0%")

        # 2. Verify ResponseBuilder formatting preserves negative edge
        from main import ResponseBuilder, _calculate_kelly_stake
        resp = ResponseBuilder.failure(
            "value_screener", "Arsenal vs Brentford", "Negative value edge",
            probability=64.2,
            edge=formatted_edge_pct,
            odds=decimal_odds,
            stake=0,
            stake_suggestion="₦0",
            selection="PASS (NO VALUE)",
            confidence="PASS"
        )
        self.assertEqual(resp["edge"], -3.37)
        self.assertEqual(resp["selection"], "PASS (NO VALUE)")
        self.assertEqual(resp["stake_suggestion"], "₦0")
        self.assertEqual(resp["pattern_matched"], False)

        # 3. Kelly stake check
        stake = _calculate_kelly_stake(model_prob, decimal_odds, 1000000.0)
        self.assertEqual(stake, 0.0)

        # 4. Verify static app.js has no hardcoded '0.0%' fallback for edgeVal
        with open("static/app.js", "r") as f:
            app_js = f.read()
        self.assertNotIn('if (edgeVal) edgeVal.textContent = "0.0%";', app_js)
        self.assertIn('edgeVal.textContent = edgeNum > 0 ? `+${edgeNum.toFixed(2)}%` : `${edgeNum.toFixed(2)}%`;', app_js)

    # REGRESSION-022: Zero Synthetic/Mock Fixture Leakage in Production Daily Slate
    async def test_regression_022_no_synthetic_fixtures_in_daily_slate(self):
        resp = await self.client.get("/api/calendar/daily-slate?date=2026-10-24")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        matches = data.get("matches", [])
        for m in matches:
            ext_id = str(m.get("external_id", ""))
            self.assertFalse(ext_id.startswith("SYN-"), f"Found synthetic fixture ID: {ext_id}")
            self.assertFalse(ext_id.startswith("auto_"), f"Found auto fixture ID: {ext_id}")
            self.assertFalse(m.get("is_synthetic", False), f"Fixture marked synthetic: {m}")

    # REGRESSION-023: Provider Error Propagation Without Fabrication
    async def test_regression_023_provider_error_propagation_honest_empty_state(self):
        from unittest.mock import patch, AsyncMock
        from football_ingestion import FootballDataService
        from main import calendar_slate_cache, app
        import httpx

        calendar_slate_cache.clear()

        mock_service = FootballDataService(api_key="mock_suspended_key")
        mock_service.last_error = "API-Sports: access: Your account is suspended"

        with patch.object(FootballDataService, "sync_all_leagues_to_db", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {
                "source": "api-sports-live",
                "status": "error",
                "message": "API-Sports: access: Your account is suspended",
                "synced_fixtures": 0
            }
            # Query using in-process ASGI client so mock is active in test process
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as local_client:
                resp = await local_client.get("/api/calendar/daily-slate?date=2029-12-31")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                # When provider fails, app must NOT fabricate fixtures
                self.assertEqual(len(data.get("matches", [])), 0, "Must return honest empty matches list, never fabricate")
                self.assertEqual(data.get("provider_status"), "error")
                self.assertTrue(any(w in str(data.get("provider_message")) for w in ["API-Sports", "suspended", "Free plans"]))


    # REGRESSION-024: Authoritative Provider Fixture Identity and Pair Integrity
    async def test_regression_024_fixture_teams_belong_to_same_record(self):
        resp = await self.client.get("/api/calendar/daily-slate?date=2026-10-24")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        matches = data.get("matches", [])
        for m in matches:
            self.assertTrue(m.get("home_team"), "Home team must be non-empty")
            self.assertTrue(m.get("away_team"), "Away team must be non-empty")
            self.assertNotEqual(m.get("home_team"), m.get("away_team"), "Home and away cannot be identical")
            # Verify top tip matches home or away team if team-specific
            top_tip = m.get("top_tip", {})
            label = top_tip.get("label", "")
            if "Win" in label and not ("Both" in label or "Draw" in label):
                team_win = label.split(":")[0].replace(" Win", "").strip()
                self.assertIn(team_win, [m["home_team"], m["away_team"]], "Prediction team must belong to fixture")

    # REGRESSION-025: Elimination of Generic Hardcoded Responses & Safe Device Slot Recycling
    async def test_regression_025_no_generic_fallbacks_and_safe_hardware_auth(self):
        # 1. Verify app.js no longer crashes on accuMinEdge or bankerCardsGrid
        with open("static/app.js", "r", encoding="utf-8") as f:
            app_js = f.read()
            self.assertIn("bankerCardsGrid: document.getElementById('bankerCardsGrid') || document.getElementById('bankersSlateGrid')", app_js)
            self.assertIn("dom.accuMinEdge ? (parseFloat(dom.accuMinEdge.value) || 0.01) : 0.01", app_js)
            self.assertIn("state.user?.bankroll", app_js)

        # 2. Verify whitelisted partner account device rotation
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as local_client:
            # Login as tip_partner with multiple different devices
            for i in range(6):
                resp = await local_client.post("/auth/login", json={
                    "username": "tip_partner",
                    "password": "TipAlgo2026!",
                    "device_id": f"partner_test_device_{i}",
                    "device_name": f"Partner Chrome {i}"
                })
                # Whitelisted partner must NEVER be blocked with 403 DEVICE_LIMIT_REACHED
                self.assertEqual(resp.status_code, 200, f"Device login {i} should succeed via seamless rotation")
                data = resp.json()
                self.assertIn("access_token", data)


if __name__ == "__main__":
    unittest.main()


