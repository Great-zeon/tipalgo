import sys
import os
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from football_ingestion import LiveFixtureItem, ProviderResult
import main
from main import (
    ALL_33_ENDPOINTS_METADATA,
    Base,
    HistoricalMatch,
    LeagueTable,
    PredictionLog,
    PredictorService,
    User,
    app,
    create_access_token,
    format_fixture_cards_helper,
    get_db,
    hash_password,
)


class _DummyPredictor:
    async def get_probabilities(self, db, home_team, away_team, league_id):
        return {
            "home_prob": 0.66,
            "draw_prob": 0.21,
            "away_prob": 0.13,
            "home_pos": 2,
            "away_pos": 16,
            "home_form_raw": "WWDWW",
            "away_form_raw": "LLDWL",
            "h2h_wins": 3,
            "source": "test_poisson",
            "home_info": {"position": 2, "points": 28, "played": 12, "goal_difference": 18, "xg_for_home": 2.3, "home_clean_sheet_pct": 0.55},
            "away_info": {"position": 16, "points": 11, "played": 12, "goal_difference": -10, "xg_against_away": 2.2, "away_failed_to_score_pct": 0.45},
            "expected_goals": {"home": 2.15, "away": 0.72, "total": 2.87},
            "all_probs": {
                "home_win": 0.66,
                "draw": 0.21,
                "away_win": 0.13,
                "double_chance_1x": 0.87,
                "double_chance_x2": 0.34,
                "over_1_5": 0.81,
                "over_2_5": 0.57,
                "under_2_5": 0.43,
                "over_3_5": 0.34,
                "btts_yes": 0.54,
                "btts_no": 0.46,
                "home_clean_sheet": 0.48,
            },
        }


@pytest.fixture
async def isolated_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as db:
        user = User(
            email="audit_tester@sportymill.com",
            username="audit_tester",
            password_hash=hash_password("StrongTest123!"),
            subscription_tier="pro",
            subscription_expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_admin=True,
            bankroll=500000.0,
        )
        db.add(user)
        # Seed LeagueTable entries for Arsenal & Bournemouth with genuine distinct form/stats
        db.add_all([
            LeagueTable(
                league_id="PL",
                team_name="Arsenal",
                position=2,
                played=14,
                wins=10,
                draws=2,
                losses=2,
                goals_for=31,
                goals_against=11,
                goal_difference=20,
                points=32,
                last_5_form="WWDWW",
                home_corners_avg=6.8,
                away_corners_avg=5.4,
                home_cards_avg=1.6,
                away_cards_avg=1.9,
                xg_for_home=2.35,
                xg_against_away=0.95,
                home_clean_sheet_pct=0.58,
                away_failed_to_score_pct=0.20,
            ),
            LeagueTable(
                league_id="PL",
                team_name="Bournemouth",
                position=15,
                played=14,
                wins=3,
                draws=4,
                losses=7,
                goals_for=13,
                goals_against=24,
                goal_difference=-11,
                points=13,
                last_5_form="LLDWL",
                home_corners_avg=4.5,
                away_corners_avg=3.9,
                home_cards_avg=2.4,
                away_cards_avg=2.8,
                xg_for_home=1.15,
                xg_against_away=2.15,
                home_clean_sheet_pct=0.22,
                away_failed_to_score_pct=0.46,
            ),
        ])
        # Seed distinct Home-only vs General historical matches so Home != General != Away
        base_dt = datetime(2026, 11, 1, 15, 0)
        hist_rows = [
            # Arsenal last 5 general: most recent AWAY L, HOME W, AWAY D, HOME W, HOME W => Gen=[L,W,D,W,W], Home=[W,W,W]
            HistoricalMatch(external_id="hist_a1", league_id="PL", home_team="Chelsea", away_team="Arsenal", match_date=base_dt + timedelta(days=14), home_score=2, away_score=1, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_a2", league_id="PL", home_team="Arsenal", away_team="Fulham", match_date=base_dt + timedelta(days=11), home_score=3, away_score=0, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_a3", league_id="PL", home_team="Everton", away_team="Arsenal", match_date=base_dt + timedelta(days=8), home_score=1, away_score=1, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_a4", league_id="PL", home_team="Arsenal", away_team="Brentford", match_date=base_dt + timedelta(days=5), home_score=2, away_score=0, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_a5", league_id="PL", home_team="Arsenal", away_team="Wolves", match_date=base_dt + timedelta(days=2), home_score=4, away_score=1, status="finished", source_classification="provider_verified"),
            # Bournemouth last 5 general: HOME W, AWAY L, AWAY L, HOME D, AWAY L => Gen=[W,L,L,D,L], Away=[L,L,L]
            HistoricalMatch(external_id="hist_b1", league_id="PL", home_team="Bournemouth", away_team="Ipswich", match_date=base_dt + timedelta(days=13), home_score=2, away_score=0, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_b2", league_id="PL", home_team="Liverpool", away_team="Bournemouth", match_date=base_dt + timedelta(days=10), home_score=3, away_score=0, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_b3", league_id="PL", home_team="Aston Villa", away_team="Bournemouth", match_date=base_dt + timedelta(days=7), home_score=2, away_score=1, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_b4", league_id="PL", home_team="Bournemouth", away_team="West Ham", match_date=base_dt + timedelta(days=4), home_score=1, away_score=1, status="finished", source_classification="provider_verified"),
            HistoricalMatch(external_id="hist_b5", league_id="PL", home_team="Newcastle", away_team="Bournemouth", match_date=base_dt + timedelta(days=1), home_score=3, away_score=1, status="finished", source_classification="provider_verified"),
        ]
        db.add_all(hist_rows)
        await db.commit()
        yield db, session_maker, user
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_fixture_item_status_dict_and_missing_date_parsing():
    # Live match with nested status dict (like live-football-api)
    live_raw = {
        "id": 900101,
        "home": {"name": "Arsenal"},
        "away": {"name": "Chelsea"},
        "kickoff": "19:45",
        "score": {"home": 2, "away": 1},
        "league": {"id": 47, "name": "Premier League", "country": "England"},
        "status": {"status": "84'", "display": "84'", "is_live": True, "state": "inprogress"},
    }
    item = LiveFixtureItem(live_raw, provider="live-football-api", fallback_date="2026-11-25")
    assert item.status == "live"
    assert item.provider_display_status == "84'"
    assert item.match_date == datetime(2026, 11, 25, 19, 45, tzinfo=timezone.utc)

    # Postponed match stays postponed
    pst_raw = {
        "id": 900102,
        "home": {"name": "Enyimba"},
        "away": {"name": "Kano Pillars"},
        "kickoff": "15:00",
        "league": {"id": 399, "name": "NPFL", "country": "Nigeria"},
        "status": {"status": "PST", "display": "Postponed", "is_live": False, "state": "postponed"},
    }
    pst_item = LiveFixtureItem(pst_raw, provider="live-football-api", fallback_date="2026-11-25")
    assert pst_item.status == "postponed"

    # Unparseable date yields None, never datetime.now()
    bad_raw = {"id": 900103, "home": {"name": "Team A"}, "away": {"name": "Team B"}, "kickoff": "TBD"}
    bad_item = LiveFixtureItem(bad_raw, provider="live-football-api", fallback_date=None)
    assert bad_item.match_date is None


@pytest.mark.asyncio
async def test_africa_lagos_calendar_midnight_uk_argentina_mexico_nigeria_and_yesterday(isolated_db):
    db, session_maker, _ = isolated_db
    # Day N = 2026-11-25 UTC includes:
    # - UK 19:45 UTC (20:45 WAT Nov 25 -> belongs to Nov 25 WAT)
    # - Nigeria 15:00 UTC (16:00 WAT Nov 25 -> belongs to Nov 25 WAT)
    # - Nigeria/Late EU 22:30 UTC (23:30 WAT Nov 25 -> belongs to Nov 25 WAT)
    # - Argentina 23:30 UTC Nov 25 (00:30 WAT Nov 26 -> belongs to Nov 26 WAT!)
    # Day N+1 = 2026-11-26 UTC includes:
    # - Mexico 03:00 UTC Nov 26 (04:00 WAT Nov 26 -> belongs to Nov 26 WAT)
    nov25_matches = [
        {"id": 1001, "home": {"name": "Arsenal"}, "away": {"name": "Bournemouth"}, "kickoff": "19:45", "league": {"id": 47, "name": "Premier League", "country": "England"}, "status": {"status": "FT", "state": "finished"}, "score": {"home": 2, "away": 0}},
        {"id": 1002, "home": {"name": "Remo Stars"}, "away": {"name": "Shooting Stars"}, "kickoff": "15:00", "league": {"id": 399, "name": "NPFL", "country": "Nigeria"}, "status": {"status": "NS", "state": "notstarted"}},
        {"id": 1003, "home": {"name": "Flamengo"}, "away": {"name": "Palmeiras"}, "kickoff": "22:30", "league": {"id": 268, "name": "Serie A", "country": "Brazil"}, "status": {"status": "NS", "state": "notstarted"}},
        {"id": 1004, "home": {"name": "Boca Juniors"}, "away": {"name": "River Plate"}, "kickoff": "23:30", "league": {"id": 112, "name": "Liga Profesional", "country": "Argentina"}, "status": {"status": "NS", "state": "notstarted"}},
    ]
    nov26_matches = [
        {"id": 1005, "home": {"name": "Club America"}, "away": {"name": "Chivas"}, "kickoff": "03:00", "league": {"id": 230, "name": "Liga MX", "country": "Mexico"}, "status": {"status": "NS", "state": "notstarted"}},
    ]
    nov24_matches = [
        {"id": 1000, "home": {"name": "Real Madrid"}, "away": {"name": "Sevilla"}, "kickoff": "20:00", "league": {"id": 87, "name": "LaLiga", "country": "Spain"}, "status": {"status": "FT", "state": "finished"}, "score": {"home": 3, "away": 1}},
    ]

    async def fake_fetch(service, target_date, refresh=False):
        by_date = {"2026-11-24": nov24_matches, "2026-11-25": nov25_matches, "2026-11-26": nov26_matches}
        rows = by_date.get(target_date, [])
        return ProviderResult(success=True, status_code=200, matches=rows)

    async def override_db():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    try:
        with patch.object(main, "_fetch_date_with_raw_cache", side_effect=fake_fetch):
            main.calendar_slate_cache.clear()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r25 = await client.get("/api/calendar/daily-slate?date=2026-11-25&endpoint=all")
                assert r25.status_code == 200
                d25 = r25.json()
                ids_25 = {m["external_id"] for m in d25["matches"]}
                assert ids_25 == {"1001", "1002", "1003"}  # 23:30 UTC Boca vs River is Nov 26 WAT, NOT Nov 25!
                uk_card = next(m for m in d25["matches"] if m["external_id"] == "1001")
                assert uk_card["time_wat"] == "20:45"
                assert uk_card["match_date_wat"] == "2026-11-25"
                assert uk_card["status"] == "finished"

                r26 = await client.get("/api/calendar/daily-slate?date=2026-11-26&endpoint=all")
                assert r26.status_code == 200
                d26 = r26.json()
                by_id_26 = {m["external_id"]: m for m in d26["matches"]}
                assert "1004" in by_id_26  # Argentina 23:30 UTC Nov 25 appears on Nov 26 at 00:30 WAT!
                assert by_id_26["1004"]["time_wat"] == "00:30"
                assert by_id_26["1004"]["match_date_wat"] == "2026-11-26"
                assert "1005" in by_id_26  # Mexico 03:00 UTC Nov 26 -> 04:00 WAT Nov 26
                assert by_id_26["1005"]["time_wat"] == "04:00"

                r24 = await client.get("/api/calendar/daily-slate?date=2026-11-24&endpoint=all")
                assert r24.status_code == 200
                d24 = r24.json()
                assert [m["external_id"] for m in d24["matches"]] == ["1000"]
                assert d24["matches"][0]["status"] == "finished"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_independent_general_home_away_form_never_duplicated(isolated_db):
    db, _, _ = isolated_db
    item = LiveFixtureItem(
        {
            "id": 777101,
            "home": {"name": "Arsenal"},
            "away": {"name": "Bournemouth"},
            "kickoff": "15:00",
            "league": {"id": 47, "name": "Premier League", "country": "England"},
            "status": {"status": "NS", "state": "notstarted"},
            "odds": {"home": 1.43, "draw": 4.50, "away": 7.20},
        },
        provider="live-football-api",
        fallback_date="2026-11-28",
    )
    cards, _ = await format_fixture_cards_helper([item], db, _DummyPredictor(), endpoint="all")
    assert len(cards) == 1
    c = cards[0]
    assert c["home_form"] == ["L", "W", "D", "W", "W"]
    assert c["home_venue_form"] == ["W", "W", "W"]  # strictly home-only, NOT copied from general
    assert c["home_venue_form"] != c["home_form"]
    assert c["away_form"] == ["W", "L", "L", "D", "L"]
    assert c["away_venue_form"] == ["L", "L", "L"]  # strictly away-only, NOT copied from general
    assert c["away_venue_form"] != c["away_form"]

    # Double Chance odd vs Straight Win odd: Home=1.43, Draw=4.50 -> 1X ~1.12, never 1.43
    assert c["endpoint_tips"]["home-win"]["odds"] == 1.43
    assert c["endpoint_tips"]["double-chance"]["odds"] == 1.12
    assert c["endpoint_tips"]["strong-home-dc"]["odds"] == 1.12
    # Every single one of all 33 endpoints is present
    expected_ids = {ep["id"] for ep in ALL_33_ENDPOINTS_METADATA}
    assert set(c["endpoint_tips"].keys()) == expected_ids


@pytest.mark.asyncio
async def test_pre_match_prediction_locked_after_live_and_full_time(isolated_db):
    db, _, _ = isolated_db
    pre_item = LiveFixtureItem(
        {
            "id": 888202,
            "home": {"name": "Arsenal"},
            "away": {"name": "Bournemouth"},
            "kickoff": "15:00",
            "league": {"id": 47, "name": "Premier League", "country": "England"},
            "status": {"status": "NS", "state": "notstarted"},
            "odds": {"home": 1.43, "draw": 4.50, "away": 7.20},
        },
        provider="live-football-api",
        fallback_date="2026-11-28",
    )
    pre_cards, _ = await format_fixture_cards_helper([pre_item], db, _DummyPredictor(), endpoint="all")
    locked_pick = pre_cards[0]["top_tip"]["label"]
    locked_odds = pre_cards[0]["top_tip"]["odds"]
    locked_prob = pre_cards[0]["top_tip"]["prob_pct"]

    # Now mutate odds drastically + simulate match going LIVE and then FINISHED with upset score 0-2
    class _PostMatchShiftedPredictor:
        async def get_probabilities(self, db, home_team, away_team, league_id):
            return {
                "home_prob": 0.12,
                "draw_prob": 0.18,
                "away_prob": 0.70,
                "home_pos": 2,
                "away_pos": 15,
                "home_info": {},
                "away_info": {},
                "expected_goals": {"home": 0.5, "away": 2.3, "total": 2.8},
                "all_probs": {"home_win": 0.12, "draw": 0.18, "away_win": 0.70, "double_chance_1x": 0.30, "double_chance_x2": 0.88, "over_1_5": 0.85, "over_2_5": 0.62, "under_2_5": 0.38, "over_3_5": 0.35, "btts_yes": 0.45, "btts_no": 0.55, "home_clean_sheet": 0.10},
            }

    ft_item = LiveFixtureItem(
        {
            "id": 888202,
            "home": {"name": "Arsenal"},
            "away": {"name": "Bournemouth"},
            "kickoff": "15:00",
            "score": {"home": 0, "away": 2},
            "league": {"id": 47, "name": "Premier League", "country": "England"},
            "status": {"status": "FT", "state": "finished"},
            "odds": {"home": 5.50, "draw": 3.90, "away": 1.45},
        },
        provider="live-football-api",
        fallback_date="2026-11-28",
    )
    ft_cards, _ = await format_fixture_cards_helper([ft_item], db, _PostMatchShiftedPredictor(), endpoint="all")
    assert ft_cards[0]["prediction_locked"] is True
    assert ft_cards[0]["top_tip"]["label"] == locked_pick
    assert ft_cards[0]["top_tip"]["odds"] == locked_odds
    assert ft_cards[0]["top_tip"]["prob_pct"] == locked_prob
    # Also confirm PredictionLog stored row kept pre-match odds and settled outcome as LOST
    log_row = (await db.execute(select(PredictionLog).where(PredictionLog.provider_fixture_id == "888202"))).scalar_one()
    assert json.loads(log_row.market_probs_json)["odds"] == locked_odds
    assert log_row.confidence_percent == locked_prob
    assert log_row.outcome in ("LOST", "incorrect")


@pytest.mark.asyncio
async def test_all_33_predict_endpoints_independently_and_contact_form(isolated_db):
    db, session_maker, user = isolated_db
    token = create_access_token({"sub": str(user.id)})
    headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.state.predictor = PredictorService()
    app.state.screener = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify Structured Contact Form Validation
            bad_contact = await client.post("/api/support/contact", json={"name": "A", "email": "bad-email", "phone": "123", "message": "short"})
            assert bad_contact.status_code == 422

            ok_contact = await client.post(
                "/api/support/contact",
                json={"name": "Tunde Adeyemi", "email": "tunde@example.com", "phone": "+234 803 123 4567", "message": "Please check Premier League odds sync for Arsenal match."},
            )
            assert ok_contact.status_code == 200
            assert ok_contact.json()["status"] == "received"
            assert ok_contact.json()["ticket_id"].startswith("SM-")

            # Independently query ALL 33 /predict/* endpoints with strict domain parameters
            base = {"home_team": "Arsenal", "away_team": "Bournemouth", "league_id": "PL", "bankroll": 100000.0}
            endpoint_payloads = [
                ("/predict/home-win", {**base, "home_odds": 1.65}),
                ("/predict/draw", {**base, "draw_odds": 4.20}),
                ("/predict/away-win", {**base, "away_odds": 5.50}),
                ("/predict/double-chance", {**base, "home_odds": 1.43, "draw_odds": 4.50, "away_odds": 7.20}),
                ("/predict/over-1-5", {**base, "over_odds": 1.28}),
                ("/predict/over-2-5", {**base, "over_odds": 1.95}),
                ("/predict/over-3-5", {**base, "over_odds": 3.40}),
                ("/predict/btts-yes", {**base, "btts_odds": 2.05}),
                ("/predict/btts-no", {**base, "btts_no_odds": 1.90}),
                ("/predict/ht-result", {**base, "h_odds": 2.30, "d_odds": 2.25, "a_odds": 5.00}),
                ("/predict/win-to-nil", {**base, "odds": 2.85}),
                ("/predict/correct-score", dict(base)),
                ("/predict/corners-total", {**base, "line": 9.5, "over_odds": 1.90, "under_odds": 1.90}),
                ("/predict/corners-1x2", dict(base)),
                ("/predict/cards-total", {**base, "line": 3.5, "over_odds": 1.85, "under_odds": 1.95}),
                ("/predict/cards-1x2", {**base, "home_odds": 2.10, "draw_odds": 4.00, "away_odds": 2.50}),
                ("/predict/goal-both-halves", {**base, "odds": 1.85}),
                ("/predict/team-total-goals", {**base, "target_team": "Arsenal", "venue": "home", "line": 1.5, "over_odds": 1.85, "under_odds": 1.95}),
                ("/predict/btts-over-2-5", {**base, "odds": 2.35}),
                ("/predict/away-underdog", {**base, "away_odds": 5.50}),
                ("/predict/fade-underdog", {**base, "home_odds": 1.55, "away_odds": 6.00}),
                ("/predict/form-gap", {**base, "home_odds": 1.65, "away_odds": 5.50}),
                ("/predict/underdog-trap", {**base, "home_odds": 2.20, "away_odds": 3.10}),
                ("/predict/ml-predictor", {**base, "home_odds": 1.65, "draw_odds": 4.20, "away_odds": 5.50}),
                ("/predict/super-tips", {**base, "home_odds": 1.65, "draw_odds": 4.20, "away_odds": 5.50}),
                ("/predict/sure-bet", {**base, "market_odds": {"home_win": 1.43, "draw": 4.50, "away_win": 7.20}, "min_edge": 0.01}),
                ("/predict/straight-home-win", {**base, "home_odds": 2.15, "away_odds": 3.20}),
                ("/predict/team-to-score", {**base, "home_odds": 1.43, "away_odds": 7.20}),
                ("/predict/batch-scan", {"matches": [{**base, "home_odds": 1.65, "draw_odds": 4.20, "away_odds": 5.50}], "min_edge": 0.01}),
                ("/predict/accumulator", {"target_odds": 2.5, "max_legs": 3, "min_edge": 0.01, "min_probability": 0.50}),
                ("/predict/no-three-goals", {**base, "home_odds": 1.95, "away_odds": 3.80, "no_three_odds": 1.95}),
                ("/predict/strong-home-dc", {**base, "home_odds": 1.43, "draw_odds": 4.50, "away_odds": 7.20}),
                ("/predict/value-screener", {**base, "home_odds": 1.65, "draw_odds": 4.20, "away_odds": 5.50, "min_edge": 0.02}),
            ]
            assert len(endpoint_payloads) == 33

            dc_odd_seen = None
            tts_odd_seen = None
            for path, payload in endpoint_payloads:
                if path == "/predict/accumulator":
                    # Avoid live API call inside accumulator during offline test
                    with patch.object(main, "_fetch_fixtures_for_lagos_date", AsyncMock(return_value=(ProviderResult(success=True, status_code=200, matches=[]), []))):
                        resp = await client.post(path, json=payload, headers=headers)
                else:
                    resp = await client.post(path, json=payload, headers=headers)
                assert resp.status_code == 200, f"{path} failed with {resp.status_code}: {resp.text}"
                body = resp.json()
                assert isinstance(body, dict), f"{path} non-dict body"
                if path == "/predict/double-chance" and body.get("pattern_matched"):
                    dc_odd_seen = body.get("odds")
                if path == "/predict/team-to-score" and body.get("pattern_matched"):
                    tts_odd_seen = body.get("odds")
                if path in ("/predict/correct-score", "/predict/corners-1x2") and body.get("pattern_matched"):
                    assert float(body.get("odds") or 0) > 1.0, f"{path} returned 0 odds"
                    assert float(body.get("probability") or 0) > 0.0, f"{path} returned 0 probability"

            if dc_odd_seen is not None:
                assert dc_odd_seen == 1.12
            if tts_odd_seen is not None:
                assert tts_odd_seen != 1.43
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_auto_persist_finished_history_and_club_disambiguation(isolated_db):
    db, _, _ = isolated_db
    # Seed ambiguous same-name club ("Inter") across two completely different countries/leagues:
    # - El Salvador (EL_PRIM) Inter lost 0-2 at home vs Alianza
    # - Italy (SA) Inter won 3-0 at home vs Bologna
    base_dt = datetime(2026, 11, 20, 18, 0)
    db.add_all([
        HistoricalMatch(
            external_id="hist_sv_inter_1", league_id="EL_PRIM",
            home_team="Inter", away_team="Alianza",
            home_team_id="sv_inter_99", away_team_id="sv_ali_88",
            match_date=base_dt, home_score=0, away_score=2,
            status="finished", source_classification="provider_verified",
        ),
        HistoricalMatch(
            external_id="hist_it_inter_1", league_id="SA",
            home_team="Inter", away_team="Bologna",
            home_team_id="it_inter_1", away_team_id="it_bol_2",
            match_date=base_dt + timedelta(hours=1), home_score=3, away_score=0,
            status="finished", source_classification="provider_verified",
        ),
    ])
    await db.commit()

    # Upcoming fixture for El Salvador Inter MUST show ['L'], NEVER Inter Milan's ['W']!
    sv_fixture = LiveFixtureItem(
        {
            "id": 991234,
            "home": {"id": "sv_inter_99", "name": "Inter"},
            "away": {"id": "sv_aguila_77", "name": "Águila"},
            "kickoff": "20:00",
            "league": {"id": 999, "name": "Primera Division", "country": "El Salvador"},
            "status": {"status": "NS", "state": "notstarted"},
        },
        provider="live-football-api",
        fallback_date="2026-11-26",
    )
    cards, _ = await format_fixture_cards_helper([sv_fixture], db, _DummyPredictor(), endpoint="all")
    assert len(cards) == 1
    assert cards[0]["home_form"] == ["L"]
    assert cards[0]["home_venue_form"] == ["L"]

    # Rendering a finished fixture automatically persists it into historical_matches
    ft_fixture = LiveFixtureItem(
        {
            "id": 995678,
            "home": {"id": "pan_udelas_1", "name": "UDELAS", "score": 2},
            "away": {"id": "pan_chor_2", "name": "Chorrillo", "score": 1},
            "kickoff": "16:00",
            "league": {"id": 888, "name": "Liga Prom", "country": "Panama"},
            "status": {"status": "FT", "state": "finished"},
        },
        provider="live-football-api",
        fallback_date="2026-11-25",
    )
    await format_fixture_cards_helper([ft_fixture], db, _DummyPredictor(), endpoint="all")
    saved_hm = (await db.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == "995678"))).scalar_one_or_none()
    assert saved_hm is not None
    assert saved_hm.home_score == 2
    assert saved_hm.away_score == 1
    assert saved_hm.home_team_id == "pan_udelas_1"
    assert saved_hm.source_classification == "provider_verified"

    # Canonical cross-feed name matching: "Atl. Mineiro" matches "Atlético Mineiro" (BSA) & "Medellín" matches "Independiente Medellín" (COL)
    main._invalidate_history_form_index()
    base_d = datetime(2026, 10, 1, 20, 0)
    db.add_all([
        HistoricalMatch(external_id="espn_bra_1", league_id="BSA", home_team="Atlético Mineiro", away_team="Flamengo", match_date=base_d + timedelta(days=5), home_score=2, away_score=1, status="finished", source_classification="provider_verified"),
        HistoricalMatch(external_id="live_bra_1", league_id="BSA", home_team="Atl. Mineiro", away_team="Flamengo", match_date=base_d + timedelta(days=5), home_score=2, away_score=1, status="finished", source_classification="provider_verified"),  # duplicate signature across feeds must dedup!
        HistoricalMatch(external_id="espn_bra_2", league_id="BSA", home_team="Palmeiras", away_team="Atlético Mineiro", match_date=base_d + timedelta(days=10), home_score=1, away_score=1, status="finished", source_classification="provider_verified"),
        HistoricalMatch(external_id="espn_col_1", league_id="COL", home_team="Independiente Medellín", away_team="Cali", match_date=base_d + timedelta(days=8), home_score=3, away_score=0, status="finished", source_classification="provider_verified"),
    ])
    await db.commit()
    main._invalidate_history_form_index()
    bra_col_fixtures = [
        LiveFixtureItem({"id": 996001, "home": {"id": "opta_1", "name": "Atl. Mineiro"}, "away": {"id": "opta_2", "name": "Palmeiras"}, "kickoff": "23:00", "league": {"id": 630, "name": "Serie A", "country": "Brazil"}, "status": {"status": "NS", "state": "notstarted"}}, provider="live-football-api", fallback_date="2026-11-26"),
        LiveFixtureItem({"id": 996002, "home": {"id": "opta_3", "name": "Medellín"}, "away": {"id": "opta_4", "name": "Cali"}, "kickoff": "23:30", "league": {"id": 650, "name": "Primera A", "country": "Colombia"}, "status": {"status": "NS", "state": "notstarted"}}, provider="live-football-api", fallback_date="2026-11-26"),
    ]
    bra_col_cards, _ = await format_fixture_cards_helper(bra_col_fixtures, db, _DummyPredictor(), endpoint="all")
    assert bra_col_cards[0]["home_form"] == ["D", "W"]  # deduplicated + canonicalized!
    assert bra_col_cards[0]["home_venue_form"] == ["W"]
    assert bra_col_cards[1]["home_form"] == ["W"]
    assert bra_col_cards[1]["home_venue_form"] == ["W"]

