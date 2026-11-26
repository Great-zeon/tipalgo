import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from main import (
    Base,
    HistoricalMatch,
    LeagueTable,
    PredictorService,
    ensure_recent_history_populated,
    format_fixture_cards_helper,
    get_both_teams_recent_matches,
    get_h2h_home_wins,
    get_team_recent_matches,
    get_team_venue_form,
)
from team_form_integrity_service import (
    LiveFootballHistoryClient,
    TeamFormIntegrityEngine,
    compute_independent_h2h,
    compute_independent_team_form,
    validate_provider_historical_fixture,
)


def _lfa_match(
    fid: str,
    date_str: str,
    home_name: str,
    away_name: str,
    hs,
    as_,
    *,
    home_id: str = "100",
    away_id: str = "200",
    status: str = "FT",
    comp: str = "Serie A",
    country: str = "Brazil",
    league_id: str = "1165",
    provider: str = "live-football-api",
    source_classification: str = "provider_verified",
):
    return {
        "id": fid,
        "provider": provider,
        "source_classification": source_classification,
        "date": date_str,
        "status": status,
        "league": {"id": league_id, "name": comp, "country": country},
        "home": {"id": home_id, "name": home_name, "score": hs},
        "away": {"id": away_id, "name": away_name, "score": as_},
    }


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


# ==============================================================================
# A. Overall Form Cases
# ==============================================================================
def test_overall_form_all_wins_all_losses_all_draws_mixed_partial_and_empty():
    cutoff = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)

    # 5 Wins
    wins = [
        _lfa_match(f"w{i}", f"2026-11-{10 + i:02d} 20:00", "Alpha FC", f"Opp {i}", 2, 0, home_id="10", away_id=str(300 + i))
        for i in range(5)
    ]
    res_w = compute_independent_team_form(wins, team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_w["symbols"] == ["W", "W", "W", "W", "W"]
    assert res_w["verified_count"] == 5
    assert res_w["status"] == "complete"

    # 5 Losses
    losses = [
        _lfa_match(f"l{i}", f"2026-11-{10 + i:02d} 20:00", "Alpha FC", f"Opp {i}", 0, 1, home_id="10", away_id=str(300 + i))
        for i in range(5)
    ]
    res_l = compute_independent_team_form(losses, team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_l["symbols"] == ["L", "L", "L", "L", "L"]

    # 5 Draws
    draws = [
        _lfa_match(f"d{i}", f"2026-11-{10 + i:02d} 20:00", f"Opp {i}", "Alpha FC", 1, 1, home_id=str(300 + i), away_id="10")
        for i in range(5)
    ]
    res_d = compute_independent_team_form(draws, team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_d["symbols"] == ["D", "D", "D", "D", "D"]

    # Mixed WDLWL ordered newest-to-oldest
    mixed = [
        _lfa_match("m1", "2026-11-24 20:00", "Alpha FC", "Opp1", 3, 1, home_id="10", away_id="11"),  # W
        _lfa_match("m2", "2026-11-20 20:00", "Opp2", "Alpha FC", 2, 2, home_id="12", away_id="10"),  # D
        _lfa_match("m3", "2026-11-16 20:00", "Alpha FC", "Opp3", 0, 2, home_id="10", away_id="13"),  # L
        _lfa_match("m4", "2026-11-12 20:00", "Opp4", "Alpha FC", 0, 1, home_id="14", away_id="10"),  # W
        _lfa_match("m5", "2026-11-08 20:00", "Alpha FC", "Opp5", 1, 2, home_id="10", away_id="15"),  # L
        _lfa_match("m0", "2026-11-01 20:00", "Alpha FC", "Opp0", 4, 0, home_id="10", away_id="16"),  # older
    ]
    res_m = compute_independent_team_form(mixed, team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_m["symbols"] == ["W", "D", "L", "W", "L"]
    assert res_m["matches"][0]["fixture_id"] == "m1"
    assert res_m["matches"][-1]["fixture_id"] == "m5"

    # Partial (< 5 games -> never padded!)
    res_p = compute_independent_team_form(mixed[:3], team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_p["symbols"] == ["W", "D", "L"]
    assert res_p["verified_count"] == 3
    assert res_p["status"] == "partial"
    assert "3/5" in res_p["label"]

    # Empty (0 games -> unavailable)
    res_empty = compute_independent_team_form([], team_name="Alpha FC", team_id="10", target_kickoff_utc=cutoff)
    assert res_empty["symbols"] == []
    assert res_empty["verified_count"] == 0
    assert res_empty["status"] == "unavailable"
    assert res_empty["label"] == "Recent form unavailable"


# ==============================================================================
# B. Home & Away Venue Isolation and Team W/D/L Perspective
# ==============================================================================
def test_home_away_venue_isolation_and_perspective():
    cutoff = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
    # Alpha FC loses at home every time, wins away every time -> home form != away form != overall form!
    matches = [
        _lfa_match("g6", "2026-11-24 20:00", "Opp1", "Alpha FC", 0, 2, home_id="21", away_id="10"),  # Away W
        _lfa_match("g5", "2026-11-20 20:00", "Alpha FC", "Opp2", 0, 1, home_id="10", away_id="22"),  # Home L
        _lfa_match("g4", "2026-11-16 20:00", "Opp3", "Alpha FC", 1, 3, home_id="23", away_id="10"),  # Away W
        _lfa_match("g3", "2026-11-12 20:00", "Alpha FC", "Opp4", 1, 2, home_id="10", away_id="24"),  # Home L
        _lfa_match("g2", "2026-11-08 20:00", "Opp5", "Alpha FC", 0, 1, home_id="25", away_id="10"),  # Away W
        _lfa_match("g1", "2026-11-04 20:00", "Alpha FC", "Opp6", 2, 2, home_id="10", away_id="26"),  # Home D
    ]

    overall = compute_independent_team_form(matches, team_name="Alpha FC", team_id="10", venue=None, target_kickoff_utc=cutoff)
    home_only = compute_independent_team_form(matches, team_name="Alpha FC", team_id="10", venue="home", target_kickoff_utc=cutoff)
    away_only = compute_independent_team_form(matches, team_name="Alpha FC", team_id="10", venue="away", target_kickoff_utc=cutoff)

    assert overall["symbols"] == ["W", "L", "W", "L", "W"]
    assert home_only["symbols"] == ["L", "L", "D"]
    assert all(m["venue"] == "home" for m in home_only["matches"])
    assert away_only["symbols"] == ["W", "W", "W"]
    assert all(m["venue"] == "away" for m in away_only["matches"])

    # Opposite perspective on the exact same match g6 (Opp1 0-2 Alpha FC) -> Opp1 got L!
    opp1_home = compute_independent_team_form(matches, team_name="Opp1", team_id="21", venue="home", target_kickoff_utc=cutoff)
    assert opp1_home["symbols"] == ["L"]
    assert opp1_home["matches"][0]["team_goals"] == 0
    assert opp1_home["matches"][0]["opp_goals"] == 2
    assert opp1_home["matches"][0]["opponent"] == "Alpha FC"


# ==============================================================================
# C. Boundary, Exclusions, Timezone & Mixed Competitions
# ==============================================================================
def test_kickoff_boundary_exclusions_timezone_and_competitions():
    # Match in Brazil/Argentina at 2026-11-25 23:00 UTC (= 2026-11-26 00:00 WAT)
    target_ko = datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)
    records = [
        # Target fixture itself -> must NEVER enter form even if already FT
        _lfa_match("target_99", "2026-11-25 23:00", "Atl. Mineiro", "Corinthians", 1, 1, home_id="1209", away_id="69157"),
        # Future completed match after kickoff -> must NEVER leak
        _lfa_match("future_1", "2026-11-29 22:30", "Atl. Mineiro", "Vasco", 3, 0, home_id="1209", away_id="999"),
        # Same UTC day later than cutoff -> excluded
        _lfa_match("later_same_day", "2026-11-25 23:30", "Atl. Mineiro", "Bahia", 2, 0, home_id="1209", away_id="888"),
        # Unfinished / postponed / cancelled / live matches -> excluded
        _lfa_match("live_1", "2026-11-24 20:00", "Atl. Mineiro", "Gremio", 1, 0, home_id="1209", away_id="777", status="1H"),
        _lfa_match("pst_1", "2026-11-23 20:00", "Atl. Mineiro", "Cruzeiro", 0, 0, home_id="1209", away_id="776", status="PST"),
        _lfa_match("canc_1", "2026-11-22 20:00", "Atl. Mineiro", "Palmeiras", 1, 0, home_id="1209", away_id="775", status="CANC"),
        # Non-senior U20 team -> excluded
        _lfa_match("u20_1", "2026-11-22 15:00", "Atl. Mineiro U20", "Corinthians U20", 4, 0, home_id="1209", away_id="774", comp="Brasileiro U20"),
        # Authentic completed senior matches across Cup, Sudamericana, and Serie A strictly before cutoff
        _lfa_match("1388261", "2026-11-21 22:30", "Atl. Mineiro", "Boca Juniors", 0, 2, home_id="1209", away_id="17958", comp="CONMEBOL Sudamericana"),
        _lfa_match("879527", "2026-11-14 21:30", "Mirassol", "Atl. Mineiro", 0, 0, home_id="5001", away_id="1209", comp="Serie A"),
        _lfa_match("887644", "2026-11-07 21:00", "Atl. Mineiro", "Grêmio", 1, 1, home_id="1209", away_id="5002", comp="Copa do Brasil"),
    ]

    form = compute_independent_team_form(
        records,
        team_name="Atl. Mineiro",
        team_id="1209",
        target_kickoff_utc=target_ko,
        exclude_fixture_id="target_99",
    )
    assert [m["fixture_id"] for m in form["matches"]] == ["1388261", "879527", "887644"]
    assert form["symbols"] == ["L", "D", "D"]
    assert [m["competition"] for m in form["matches"]] == [
        "CONMEBOL Sudamericana",
        "Serie A",
        "Copa do Brasil",
    ]


# ==============================================================================
# D. Validation, Provenance Exclusivity & Deduplication
# ==============================================================================
def test_validation_rejects_seed_espn_bad_scores_and_duplicates():
    # Reject ESPN, seed, manual, synthetic IDs, boolean scores, missing scores
    assert validate_provider_historical_fixture(_lfa_match("espn_1", "2026-11-20 15:00", "Arsenal", "Chelsea", 2, 1, provider="espn-public")) is None
    assert validate_provider_historical_fixture(_lfa_match("seed_1", "2026-11-20 15:00", "Arsenal", "Chelsea", 2, 1, provider="standings_seed", source_classification="seed")) is None
    assert validate_provider_historical_fixture(_lfa_match("SYN-123", "2026-11-20 15:00", "Arsenal", "Chelsea", 2, 1)) is None
    assert validate_provider_historical_fixture(_lfa_match("123456", "2026-11-20 15:00", "Arsenal", "Chelsea", None, 1)) is None
    assert validate_provider_historical_fixture(_lfa_match("123457", "2026-11-20 15:00", "Arsenal", "Chelsea", True, 1)) is None
    assert validate_provider_historical_fixture(_lfa_match("123458", "2026-11-20 15:00", "Arsenal", "Chelsea", -1, 1)) is None
    # Legacy 25-char base36 hex IDs lacking team IDs (ambiguous clubs + WAT-shifted dates) must be rejected
    assert validate_provider_historical_fixture(_lfa_match("dy4takngn3a54mps14x3ddi50", "2026-11-07 23:00", "Independiente", "Real Tomayapo", 0, 2, home_id="", away_id="")) is None

    # Deduplicate same fixture_id and same-day / ±48h legacy hex duplicate (numeric ID always wins)
    dups = [
        _lfa_match("55501", "2026-11-20 18:00", "Boca Juniors", "River Plate", 1, 0, home_id="17958", away_id="9122"),
        _lfa_match("55501", "2026-11-20 18:00", "Boca Juniors", "River Plate", 1, 0, home_id="17958", away_id="9122"),
        _lfa_match("55502", "2026-11-20 18:00", "Boca Juniors", "CA River Plate", 1, 0, home_id="17958", away_id="9122"),
        _lfa_match("f3muqo8nhjkxwqgv9kmrwnuhg", "2026-11-08 21:00", "Atl. Mineiro", "Grêmio", 1, 1, home_id="1209", away_id="5002"),
        _lfa_match("887644", "2026-11-07 21:00", "Atl. Mineiro", "Grêmio", 1, 1, home_id="1209", away_id="5002"),
    ]
    res = compute_independent_team_form(dups, team_name="Boca Juniors", team_id="17958")
    assert res["verified_count"] == 1
    assert res["symbols"] == ["W"]
    res_gremio = compute_independent_team_form(dups, team_name="Atl. Mineiro", team_id="1209")
    assert res_gremio["verified_count"] == 1
    assert res_gremio["matches"][0]["fixture_id"] == "887644"


# ==============================================================================
# E. Provider Failure Handling & Season Fallback
# ==============================================================================
@pytest.mark.asyncio
async def test_provider_failure_modes_and_season_fallback(async_db):
    # 1. Zero credits -> INSUIFFICIENT_CREDITS surfaced, no fallback
    async def mock_zero_credits(url):
        return (403, {"success": False, "message": "Insufficient credits remaining"})

    eng_err = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key="a" * 48, http_get=mock_zero_credits))
    out_credits = await eng_err.get_team_form(async_db, team_name="Atl. Mineiro", team_id="1209")
    assert out_credits["status"] == "provider_error"
    assert out_credits["error_code"] == "INSUFFICIENT_CREDITS"
    assert out_credits["symbols"] == []

    # 2. Invalid auth key -> AUTH_FAILURE
    async def mock_auth_fail(url):
        return (401, {"success": False, "message": "Invalid API key"})

    eng_auth = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key="a" * 48, http_get=mock_auth_fail))
    out_auth = await eng_auth.get_team_form(async_db, team_name="Boca Juniors", team_id="17958")
    assert out_auth["status"] == "provider_error"
    assert out_auth["error_code"] == "AUTH_FAILURE"

    # 3. Malformed payload -> MALFORMED_PAYLOAD
    async def mock_malformed(url):
        return (200, "<<<NOT JSON>>>")

    eng_bad = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key="a" * 48, http_get=mock_malformed))
    out_bad = await eng_bad.get_team_form(async_db, team_name="Boca Juniors", team_id="17958")
    assert out_bad["status"] == "provider_error"
    assert out_bad["error_code"] == "MALFORMED_PAYLOAD"

    # 4. Timeout -> retries then PROVIDER_TIMEOUT
    calls = {"n": 0}
    async def mock_timeout(url):
        calls["n"] += 1
        raise TimeoutError("upstream timeout")

    eng_to = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key="a" * 48, http_get=mock_timeout))
    out_to = await eng_to.get_team_form(async_db, team_name="Boca Juniors", team_id="17958")
    assert calls["n"] == 3  # initial + 2 retries
    assert out_to["status"] == "provider_error"
    assert out_to["error_code"] == "PROVIDER_TIMEOUT"

    # 5. Season fallback ("2027" empty -> automatically fetches "2026")
    seen_urls = []
    async def mock_season_fallback(url):
        seen_urls.append(url)
        if "season=2026" in url:
            return (200, {
                "success": True,
                "credits_remaining": 50,
                "data": {
                    "team_id": 17958,
                    "season": "2026",
                    "available_seasons": ["2027", "2026"],
                    "matches": [
                        _lfa_match("1388261", "2026-11-21 22:30", "Atl. Mineiro", "Boca Juniors", 0, 2, home_id="1209", away_id="17958"),
                        _lfa_match("977867", "2026-11-14 22:00", "Banfield", "Boca Juniors", 0, 3, home_id="1190", away_id="17958"),
                    ],
                },
            })
        return (200, {
            "success": True,
            "credits_remaining": 51,
            "data": {
                "team_id": 17958,
                "season": "2027",
                "available_seasons": ["2027", "2026"],
                "matches": [],
            },
        })

    eng_fb = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key="a" * 48, http_get=mock_season_fallback))
    out_fb = await eng_fb.get_team_form(
        async_db, team_name="Boca Juniors", team_id="17958",
        before_utc=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc),
    )
    assert len(seen_urls) == 2
    assert out_fb["symbols"] == ["W", "W"]
    assert out_fb["status"] == "partial"


# ==============================================================================
# F. Prediction Engine, Card Helper & DB Provenance Non-Leakage
# ==============================================================================
@pytest.mark.asyncio
async def test_db_seed_espn_rejected_and_prediction_uses_live_football_api_only(async_db, monkeypatch):
    # Insert toxic rows: seeded LeagueTable.last_5_form, espn-public HistoricalMatch, future match
    async_db.add(LeagueTable(
        league_id="BRA_SERI", team_name="Atl. Mineiro", position=5, played=30,
        wins=15, draws=8, losses=7, goals_for=45, goals_against=30, goal_difference=15,
        points=53, last_5_form="LLLLL",
    ))
    async_db.add(LeagueTable(
        league_id="BRA_SERI", team_name="Corinthians", position=8, played=30,
        wins=12, draws=9, losses=9, goals_for=35, goals_against=32, goal_difference=3,
        points=45, last_5_form="LLLLL",
    ))
    # Toxic ESPN row
    async_db.add(HistoricalMatch(
        external_id="espn_99911",
        home_team="Atl. Mineiro", away_team="Flamengo",
        home_team_id="espn_t_1", away_team_id="espn_t_2",
        home_score=5, away_score=0,
        league_id="BRA_SERI", match_date=datetime(2026, 11, 22, 18, 0),
        status="finished", provider="espn-public", source_classification="provider_verified",
    ))
    # Toxic seed row
    async_db.add(HistoricalMatch(
        external_id="SEED-888",
        home_team="Atl. Mineiro", away_team="Bahia",
        home_team_id="1209", away_team_id="888",
        home_score=4, away_score=0,
        league_id="BRA_SERI", match_date=datetime(2026, 11, 23, 18, 0),
        status="finished", provider="standings_seed", source_classification="seed",
    ))
    # Authentic Live Football API matches strictly before Nov 25 23:00 target kickoff
    async_db.add(HistoricalMatch(
        external_id="1388261",
        home_team="Atl. Mineiro", away_team="Boca Juniors",
        home_team_id="1209", away_team_id="17958",
        home_score=0, away_score=2,
        league_id="SOU_CONM", match_date=datetime(2026, 11, 21, 22, 30),
        status="finished", provider="live-football-api", source_classification="provider_verified",
        provider_status="FT", raw_payload=json.dumps({"league": {"name": "CONMEBOL Sudamericana"}}),
    ))
    async_db.add(HistoricalMatch(
        external_id="879527",
        home_team="Mirassol", away_team="Atl. Mineiro",
        home_team_id="5001", away_team_id="1209",
        home_score=0, away_score=0,
        league_id="BRA_SERI", match_date=datetime(2026, 11, 14, 21, 30),
        status="finished", provider="live-football-api", source_classification="provider_verified",
        provider_status="FT", raw_payload=json.dumps({"league": {"name": "Serie A"}}),
    ))
    # The target fixture itself (finished after kickoff) -> MUST NOT enter pre-match form
    async_db.add(HistoricalMatch(
        external_id="879546",
        home_team="Atl. Mineiro", away_team="Corinthians",
        home_team_id="1209", away_team_id="69157",
        home_score=1, away_score=1,
        league_id="BRA_SERI", match_date=datetime(2026, 11, 25, 23, 0),
        status="finished", provider="live-football-api", source_classification="provider_verified",
        provider_status="FT",
    ))
    await async_db.commit()

    # Point singleton engine to an unconfigured offline client so zero external requests happen
    import team_form_integrity_service as tfis
    offline_engine = TeamFormIntegrityEngine(LiveFootballHistoryClient(api_key=""))
    monkeypatch.setattr(tfis, "_shared_integrity_engine", offline_engine)

    # 1. PredictorService ignores LeagueTable.last_5_form ("LLLLL") and excludes ESPN/seed/self match!
    pred = PredictorService()
    probs = await pred.get_probabilities(
        async_db,
        "Atl. Mineiro",
        "Corinthians",
        "BRA_SERI",
        target_kickoff_utc=datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc),
        exclude_fixture_id="879546",
        home_team_id="1209",
        away_team_id="69157",
        allow_live_fetch=False,
    )
    assert probs["home_form_raw"] == "LD"  # strictly [Boca 0-2 (L), Mirassol 0-0 (D)], NEVER "LLLLL" or ESPN/seed
    assert probs["away_form_raw"] == ""
    assert probs["form_integrity"]["home_overall"]["verified"] == 2
    assert probs["form_integrity"]["away_overall"]["verified"] == 0

    # 2. Recent match helpers exclude ESPN and Seed
    rec_all = await get_team_recent_matches(
        async_db, "Atl. Mineiro", venue=None, limit=5,
        before_utc=datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc),
        exclude_fixture_id="879546",
        team_id="1209",
    )
    assert [r["fixture_id"] for r in rec_all] == ["1388261", "879527"]
    home_v = await get_team_venue_form(
        async_db, "Atl. Mineiro", "home", limit=5,
        before_utc=datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc),
        exclude_fixture_id="879546",
        team_id="1209",
    )
    assert home_v == "L"  # Mirassol was away, Boca Juniors was home

    # 3. format_fixture_cards_helper attaches verified details, never falls back to LeagueTable.last_5_form
    target_fx = SimpleNamespace(
        id=1, external_id="879546", league_id="BRA_SERI",
        home_team="Atl. Mineiro", away_team="Corinthians",
        home_team_id="1209", away_team_id="69157",
        home_score=1, away_score=1, status="finished",
        provider_status="FT", provider_display_status="FT",
        source_classification="provider_verified",
        match_date=datetime(2026, 11, 25, 23, 0),
        home_odds=2.05, draw_odds=3.20, away_odds=3.60,
        home_logo=None, away_logo=None, raw_payload=None,
        provider_updated_at=None, synced_at=None,
    )
    cards, _ = await format_fixture_cards_helper(
        [target_fx], async_db, pred, "all", allow_live_form_fetch=False
    )
    assert len(cards) == 1
    card = cards[0]
    assert card["home_form"] == ["L", "D"]
    assert card["home_venue_form"] == ["L"]
    assert card["away_form"] == []
    assert card["away_venue_form"] == []
    assert [d["fixture_id"] for d in card["home_form_details"]] == ["1388261", "879527"]
    assert card["form_provider"] == "live-football-api"


def test_h2h_calculation_respects_cutoff_and_perspective():
    cutoff = datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc)
    records = [
        _lfa_match("8434402", "2026-11-26 00:00", "Boca Juniors", "Independiente", 2, 0, home_id="17958", away_id="1189"),  # target -> exclude!
    ]
    h2h_api_rows = [
        {"date": "2025-08-17", "home": {"id": 1189, "name": "Independiente"}, "away": {"id": 17958, "name": "Boca Juniors"}, "score": "0-3"},
        {"date": "2025-02-11", "home": {"id": 17958, "name": "Boca Juniors"}, "away": {"id": 1189, "name": "Independiente"}, "score": "2-0"},
        {"date": "2024-08-10", "home": {"id": 1189, "name": "Independiente"}, "away": {"id": 17958, "name": "Boca Juniors"}, "score": "1-1"},
    ]
    h2h = compute_independent_h2h(
        records,
        home_team="Boca Juniors",
        away_team="Independiente",
        home_team_id="17958",
        away_team_id="1189",
        target_kickoff_utc=cutoff,
        exclude_fixture_id="8434402",
        extra_h2h_rows=h2h_api_rows,
    )
    assert h2h["total_matches"] == 3
    assert h2h["home_wins"] == 2
    assert h2h["draws"] == 1
    assert h2h["away_wins"] == 0
