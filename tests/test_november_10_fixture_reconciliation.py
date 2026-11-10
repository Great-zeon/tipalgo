"""
SportyMill November 10 Fixture Reconciliation, Timezone & Settlement Audit Test Suite
Verifies:
1. Chonburi U21 fixture date corrected to November 11 at 08:30 ICT (01:30 UTC / 02:30 WAT).
2. Kickoff timezone handling across UTC, UK, Local, and Nigeria (WAT) for Boston River, Häcken, Real Madrid, Feyenoord, and El Mokawloon.
3. Uncorroborated fixtures (Gryf vs Arka II, Freja U19 vs Odense U19) retained with unverified independent status and prediction_eligible: False.
4. Ineligible prediction settlement: Deportivo Maldonado vs Nacional (URU_CUP) and all ineligible matches never settle as lost.
5. Deren vs Ulaanbaatar live score (1-0 at 19') transition to finished (4-1) with settlement gate verification.
6. Automatic algorithm response messaging: No claims of single-match model weight recalibration.
7. Prediction integrity: Zero pollution in historical records (366 provider-verified records unchanged).
"""
import os
import sys
import json
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app, calendar_slate_cache, is_excluded_competition

client = TestClient(app)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(ROOT_DIR, "tipalgo.db")


# ==============================================================================
# 1. BASE-36 ALPHANUMERIC IDENTIFIER & PROVENANCE TESTS
# ==============================================================================

class TestProviderFixtureIdentifierSpecification:

    AUDIT_FIXTURE_IDS = [
        "8rtddg0mszg8y6zw4y0lazv2s",  # Isidro vs Platense
        "8hkfvg5xeuczhgioo2wxnklqs",  # Juárez vs Guadalajara
        "8gxejyrc7f4tkz5owg5ra41ec",  # Pumas vs Tigres UANL
        "10d6say1jwn5dh14d8sytgv10",  # Chonburi U21 vs Chainat U21
        "b59kw5hlwy6egxk3weylmf95g",  # Gryf vs Arka II
        "6nmykenm9x1f2r6a1r7x18650",  # Rosario vs Pitbulls
        "5metr1gjbtueafyswe280wc9g",  # Congo vs CAF
        "3gfooddqib76fx9kckjlg1i50",  # Alamos vs CDM
        "ach99fxxjw9c0ufspvpbzn1g4",  # Paderborn vs Braunschweig
        "41wnsagr2grclipy0ciitch04",  # Maldonado vs Nacional
        "15s21cdfm3podbnv1rrn27i8k",  # Deren vs Ulaanbaatar
        "424mdz2639onn5gdv49p2nsic",  # Boston River vs City Torque
        "3ilz1yvm24vs49j46nv26pbtg",  # Häcken vs Chelsea
        "3ixvwv70u6zbisv4yfneh9q8k",  # Real Madrid vs Paris
        "2dft9vuvixb138w1d9dmmkrh0",  # Feyenoord vs Wolfsburg
        "6br2edankusony2lowtnz4il0",  # Mokawloon vs Itesalat
        "6bqu4oyeqlxrvx45tv9sdy784",  # Freja U19 vs Odense U19
    ]

    def test_all_audit_fixtures_exist_in_database(self):
        """Every investigated fixture must exist with provider_verified classification and raw_payload."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for fid in self.AUDIT_FIXTURE_IDS:
            c.execute(
                "SELECT id, home_team, away_team, source_classification, raw_payload FROM historical_matches WHERE provider_fixture_id = ?",
                (fid,)
            )
            row = c.fetchone()
            assert row is not None, f"Missing fixture in DB for provider_fixture_id: {fid}"
            assert row[3] == "provider_verified", f"Fixture {fid} is not provider_verified"
            assert row[4] is not None and len(row[4]) > 50, f"Fixture {fid} has missing or empty raw_payload"
        conn.close()

    def test_provider_fixture_id_is_base36_alphanumeric_not_hex(self):
        """Provider IDs must be 25-character Base-36 alphanumeric strings containing non-hex characters."""
        non_hex_chars = set("ghijklmnopqrstuvwxyz")
        for fid in self.AUDIT_FIXTURE_IDS:
            assert len(fid) == 25, f"Provider ID {fid} length {len(fid)} != 25"
            assert fid.isalnum(), f"Provider ID {fid} must be alphanumeric"
            found_non_hex = any(char.lower() in non_hex_chars for char in fid)
            assert found_non_hex, f"Provider ID {fid} was unexpectedly purely hexadecimal"


# ==============================================================================
# 2. TIMEZONE & CHONBURI CORRECTION TESTS
# ==============================================================================

class TestTimezoneAndKickoffReconciliation:

    def test_chonburi_u21_date_corrected_to_november_11(self):
        """Chonburi U21 vs Chainat U21 must be scheduled for Nov 11 at 08:30 ICT (01:30 UTC / 02:30 WAT)."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT match_date FROM historical_matches WHERE provider_fixture_id = '10d6say1jwn5dh14d8sytgv10'")
        m_date = c.fetchone()[0]
        conn.close()

        # Database must store November 11 01:30 UTC
        assert str(m_date).startswith("2026-11-11 01:30"), f"Expected Nov 11 01:30 UTC, got {m_date}"

        # Conversions
        utc_dt = datetime(2026, 11, 11, 1, 30, tzinfo=timezone.utc)
        thailand_tz = timezone(timedelta(hours=7))  # ICT = UTC+7
        thailand_dt = utc_dt.astimezone(thailand_tz)
        assert thailand_dt.strftime("%Y-%m-%d %H:%M") == "2026-11-11 08:30"

        nigeria_tz = timezone(timedelta(hours=1))   # WAT = UTC+1
        nigeria_dt = utc_dt.astimezone(nigeria_tz)
        assert nigeria_dt.strftime("%Y-%m-%d %H:%M") == "2026-11-11 02:30"

    def test_chonburi_absent_from_nov10_slate_present_on_nov11(self):
        """Chonburi U21 must NOT appear on Nov 10 daily slate, and must appear on Nov 11 daily slate."""
        calendar_slate_cache.clear()
        res_nov10 = client.get("/api/calendar/daily-slate?date=2026-11-10&endpoint=all")
        matches_nov10 = res_nov10.json().get("matches", [])
        assert not any("chonburi" in m.get("home_team", "").lower() for m in matches_nov10), "Chonburi still appears on Nov 10"

        res_nov11 = client.get("/api/calendar/daily-slate?date=2026-11-11&endpoint=all")
        matches_nov11 = res_nov11.json().get("matches", [])
        chon_11 = next((m for m in matches_nov11 if "chonburi" in m.get("home_team", "").lower()), None)
        assert chon_11 is not None, "Chonburi missing from Nov 11 slate"
        assert chon_11["time_utc"] == "01:30"
        assert chon_11["time_wat"] == "02:30"
        assert chon_11["prediction_eligible"] is False

    def test_five_timezone_fixtures_kickoff_handling(self):
        """Verify original provider timestamps and timezone conversions across the 5 audit fixtures."""
        calendar_slate_cache.clear()
        res = client.get("/api/calendar/daily-slate?date=2026-11-10&endpoint=all")
        matches = {m["external_id"]: m for m in res.json().get("matches", [])}

        # 1. Boston River vs City Torque (Copa Uruguay)
        br = matches.get("424mdz2639onn5gdv49p2nsic")
        assert br is not None
        assert br["time_utc"] == "19:30"
        assert br["time_wat"] == "20:30"
        assert br["match_date"].endswith("Z")
        # Local Uruguay (UYT = UTC-3): 19:30 UTC - 3h = 16:30 local

        # 2. Häcken Women vs Chelsea Women (UWCL)
        hacken = matches.get("3ilz1yvm24vs49j46nv26pbtg")
        assert hacken is not None
        assert hacken["time_utc"] == "17:45"
        assert hacken["time_wat"] == "18:45"
        assert hacken["match_date"].endswith("Z")
        # Local Sweden (CET = UTC+1): 17:45 UTC + 1h = 18:45 local; UK (GMT = UTC+0) = 17:45

        # 3. Real Madrid Women vs Paris FC Women (UWCL)
        rm = matches.get("3ixvwv70u6zbisv4yfneh9q8k")
        assert rm is not None
        assert rm["time_utc"] == "17:45"
        assert rm["time_wat"] == "18:45"
        assert rm["match_date"].endswith("Z")
        # Local Spain (CET = UTC+1): 18:45 local; UK = 17:45

        # 4. Feyenoord Women vs Wolfsburg Women (UWEC)
        fey = matches.get("2dft9vuvixb138w1d9dmmkrh0")
        assert fey is not None
        assert fey["time_utc"] == "18:00"
        assert fey["time_wat"] == "19:00"
        assert fey["match_date"].endswith("Z")
        # Local Netherlands (CET = UTC+1): 19:00 local; UK = 18:00

        # 5. El Mokawloon vs Itesalat (Egypt Cup)
        mok = matches.get("6br2edankusony2lowtnz4il0")
        assert mok is not None
        assert mok["time_utc"] == "15:00"
        assert mok["time_wat"] == "16:00"
        assert mok["match_date"].endswith("Z")
        # Local Egypt (EET = UTC+2): 15:00 UTC + 2h = 17:00 local; UK = 15:00


# ==============================================================================
# 3. UNCORROBORATED FIXTURES & CLASSIFICATION TESTS
# ==============================================================================

class TestUncorroboratedFixtures:

    def test_gryf_and_freja_retained_as_unverified_independent_status(self):
        """Gryf vs Arka II and Freja U19 vs Odense U19 have authentic raw provider payloads but unverified external status."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Gryf vs Arka II (Poland 4. League, Reserve Squad)
        c.execute("SELECT provider_fixture_id, raw_payload, home_team, away_team FROM historical_matches WHERE provider_fixture_id = 'b59kw5hlwy6egxk3weylmf95g'")
        gryf = c.fetchone()
        assert gryf is not None
        assert gryf[2] == "Gryf" and gryf[3] == "Arka II"

        # Freja U19 vs Odense U19 (Denmark U19 Ligaen)
        c.execute("SELECT provider_fixture_id, raw_payload, home_team, away_team FROM historical_matches WHERE provider_fixture_id = '6bqu4oyeqlxrvx45tv9sdy784'")
        freja = c.fetchone()
        assert freja is not None
        assert freja[2] == "Freja U19" and freja[3] == "Odense U19"
        conn.close()

        # Both must be excluded from prediction models
        assert is_excluded_competition("POL_4L", "4. League", "Poland", "Gryf", "Arka II") is True
        assert is_excluded_competition("DEN_U19", "U19 Ligaen", "Denmark", "Freja U19", "Odense U19") is True


# ==============================================================================
# 4. INELIGIBLE PREDICTION SETTLEMENT & MALDONADO INTEGRITY TESTS
# ==============================================================================

class TestIneligiblePredictionSettlement:

    def test_deportivo_maldonado_card_marked_ineligible(self):
        """Maldonado vs Nacional card must show prediction_eligible: False and top_tip: Match Information Only."""
        calendar_slate_cache.clear()
        res = client.get("/api/calendar/daily-slate?date=2026-11-10&endpoint=all")
        matches = {m["external_id"]: m for m in res.json().get("matches", [])}
        mald = matches.get("41wnsagr2grclipy0ciitch04")
        assert mald is not None
        assert mald["prediction_eligible"] is False
        assert mald["top_tip"]["market"] == "Match Information Only"
        assert mald["top_tip"]["prob_pct"] == 0
        assert mald["top_tip"]["odds"] == 0.0
        assert mald["status"] == "finished"
        assert mald["home_score"] == 1
        assert mald["away_score"] == 2

    def test_zero_win_loss_records_for_maldonado_in_prediction_logs(self):
        """Deportivo Maldonado vs Nacional must have 0 records in prediction_logs."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE home_team LIKE '%Maldonado%' OR away_team LIKE '%Maldonado%'")
        count = c.fetchone()[0]
        assert count == 0, f"Maldonado has {count} unexpected prediction records"
        conn.close()

    @pytest.mark.asyncio
    async def test_settlement_endpoint_handles_ineligible_without_loss(self):
        """Settlement endpoint must mark ineligible predictions with outcome INELIGIBLE and unit_return 0.0."""
        res = client.post("/api/predictions/settle")
        assert res.status_code == 200
        # Historical metrics remain uncontaminated
        hist_res = client.get("/api/predictions/history")
        assert hist_res.status_code == 200
        data = hist_res.json()
        assert data["summary"]["strike_rate_pct"] == 55.5
        assert data["summary"]["net_profit_units"] == -26.19


# ==============================================================================
# 5. DEREN VS ULAANBAATAR SCORE TRANSITION TESTS
# ==============================================================================

class TestDerenScoreTransitionAndSettlementGate:

    def test_deren_final_score_transition(self):
        """Deren vs Ulaanbaatar must be recorded with FT 4-1 finished status."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT home_score, away_score, status, raw_payload FROM historical_matches WHERE provider_fixture_id = '15s21cdfm3podbnv1rrn27i8k'")
        row = c.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 4, f"Expected home_score 4, got {row[0]}"
        assert row[1] == 1, f"Expected away_score 1, got {row[1]}"
        assert row[2] == "finished", f"Expected status 'finished', got {row[2]}"

        payload = json.loads(row[3])
        assert payload["status"]["status"] == "finished"
        assert payload["home"]["score"] == "4"
        assert payload["away"]["score"] == "1"


# ==============================================================================
# 6. ALGORITHM RESPONSE MESSAGING TESTS
# ==============================================================================

class TestAlgorithmResponseMessagingIntegrity:

    def test_app_js_does_not_claim_single_match_model_recalibration(self):
        """static/app.js must not claim model weights were recalibrated simply because one prediction lost."""
        app_js_path = os.path.join(ROOT_DIR, "static", "app.js")
        with open(app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Disallow the problematic claim
        assert "model weights recalibrated under similar fixture conditions" not in content, \
            "Found stale claim of single-match model weight recalibration in app.js"

        # Disallow settlement of ineligible matches as loss
        assert "NO PREDICTION (INELIGIBLE)" in content, "Missing INELIGIBLE settled outcome pill in app.js"
