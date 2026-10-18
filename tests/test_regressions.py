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

BASE_URL = "http://127.0.0.1:8000"

class TestRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=15.0)
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


if __name__ == "__main__":
    unittest.main()
