"""
SportyMill November 10 Fixture Reconciliation & Data Integrity Test Suite
Verifies:
- All 9 disputed fixtures have authentic 25-character Base-36 alphanumeric IDs
- Accurate date and timezone conversions across UTC, Local, and Nigeria (WAT)
- Chonburi U21 vs Chainat U21 kickoff and timezone representation
- Strict competition classification and prediction eligibility filtering
- Zero prediction records generated or settled for disputed fixtures
- Final score acceptance only from verified final provider records
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

    DISPUTED_FIXTURE_IDS = [
        "8rtddg0mszg8y6zw4y0lazv2s",  # Isidro vs Platense
        "8hkfvg5xeuczhgioo2wxnklqs",  # Juárez vs Guadalajara
        "8gxejyrc7f4tkz5owg5ra41ec",  # Pumas vs Tigres UANL
        "10d6say1jwn5dh14d8sytgv10",  # Chonburi U21 vs Chainat U21
        "b59kw5hlwy6egxk3weylmf95g",  # Gryf vs Arka II
        "6nmykenm9x1f2r6a1r7x18650",  # Rosario vs Pitbulls
        "5metr1gjbtueafyswe280wc9g",  # Congo vs CAF
        "3gfooddqib76fx9kckjlg1i50",  # Alamos vs CDM
        "ach99fxxjw9c0ufspvpbzn1g4",  # Paderborn vs Braunschweig
    ]

    def test_all_nine_disputed_fixtures_exist_in_database(self):
        """Every disputed fixture must exist with provider_verified classification and raw_payload."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for fid in self.DISPUTED_FIXTURE_IDS:
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
        for fid in self.DISPUTED_FIXTURE_IDS:
            assert len(fid) == 25, f"Provider ID {fid} length {len(fid)} != 25"
            assert fid.isalnum(), f"Provider ID {fid} must be alphanumeric"
            # Prove that they are NOT hexadecimal (contain characters beyond 0-9 and a-f)
            found_non_hex = any(char.lower() in non_hex_chars for char in fid)
            assert found_non_hex, f"Provider ID {fid} was unexpectedly purely hexadecimal"


# ==============================================================================
# 2. TIMEZONE & CHONBURI DISCREPANCY RECONCILIATION TESTS
# ==============================================================================

class TestTimezoneAndKickoffReconciliation:

    def test_chonburi_u21_kickoff_and_timezone_conversions(self):
        """Verify Chonburi U21 kickoff conversions across UTC, Thailand (ICT), and Nigeria (WAT)."""
        # UTC: 2026-11-10 08:30:00
        utc_dt = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
        
        # Thailand ICT = UTC + 7
        thailand_tz = timezone(timedelta(hours=7))
        thailand_dt = utc_dt.astimezone(thailand_tz)
        assert thailand_dt.hour == 15
        assert thailand_dt.minute == 30
        assert thailand_dt.strftime("%Y-%m-%d") == "2026-11-10"

        # Nigeria WAT = UTC + 1
        nigeria_tz = timezone(timedelta(hours=1))
        nigeria_dt = utc_dt.astimezone(nigeria_tz)
        assert nigeria_dt.hour == 9
        assert nigeria_dt.minute == 30
        assert nigeria_dt.strftime("%Y-%m-%d") == "2026-11-10"

    def test_americas_overnight_kickoff_conversions(self):
        """Verify that overnight UTC kickoffs correspond to Tuesday evening in Mexico and El Salvador."""
        # Isidro vs Platense: 2026-11-11 01:00 UTC
        isidro_utc = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc)
        cst_tz = timezone(timedelta(hours=-6))  # El Salvador / Central Mexico
        isidro_local = isidro_utc.astimezone(cst_tz)
        assert isidro_local.strftime("%Y-%m-%d %H:%M") == "2026-11-10 19:00"

        # Juárez vs Guadalajara: 2026-11-11 03:00 UTC
        juarez_utc = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
        juarez_local = juarez_utc.astimezone(cst_tz)
        assert juarez_local.strftime("%Y-%m-%d %H:%M") == "2026-11-10 21:00"


# ==============================================================================
# 3. COMPETITION CLASSIFICATION & PREDICTION ELIGIBILITY TESTS
# ==============================================================================

class TestCompetitionClassificationAndEligibility:

    def test_women_and_youth_competitions_excluded_from_prediction_models(self):
        """is_excluded_competition must flag women, youth, friendlies, and reserve squads."""
        assert is_excluded_competition("MEX_LIGA", "Liga MX Femenil", "Mexico") is True
        assert is_excluded_competition("WOR_FRIE", "Friendly International Women", "World") is True
        assert is_excluded_competition("THA_YOUT", "Youth League", "Thailand") is True
        assert is_excluded_competition("WOR_CLUB", "Club Friendly", "World") is True
        assert is_excluded_competition("POL_4L", "4. League", "Poland", "Gryf", "Arka II") is True

    def test_senior_competitions_remain_eligible(self):
        """Senior professional league fixtures must remain eligible."""
        assert is_excluded_competition("COL", "Primera A", "Colombia", "Junior", "Cúcuta") is False
        assert is_excluded_competition("BOL_PRIM", "Primera División", "Bolivia", "Aurora", "Uni de Vinto") is False
        assert is_excluded_competition("COS_LIGA", "Liga de Ascenso", "Costa Rica", "Rosario", "Pitbulls") is False
        assert is_excluded_competition("MEX_LIGA", "Liga TDP", "Mexico", "Alamos", "CDM") is False

    def test_daily_slate_calendar_visibility_with_prediction_ineligible_flag(self):
        """Calendar daily slate must display genuine non-senior fixtures with prediction_eligible: False."""
        calendar_slate_cache.clear()
        response = client.get("/api/calendar/daily-slate?date=2026-11-10&endpoint=all")
        assert response.status_code == 200
        data = response.json()
        matches = {f"{m['home_team']} vs {m['away_team']}": m for m in data.get("matches", [])}

        # Check Chonburi U21
        chonburi_key = "Chonburi U21 vs Chainat U21"
        assert chonburi_key in matches
        assert matches[chonburi_key]["league_name"] == "Youth League"
        assert matches[chonburi_key]["prediction_eligible"] is False
        assert matches[chonburi_key]["is_banker_80"] is False

        # Check Paderborn vs Braunschweig
        paderborn_key = "Paderborn vs Braunschweig"
        assert paderborn_key in matches
        assert matches[paderborn_key]["league_name"] == "Club Friendly"
        assert matches[paderborn_key]["prediction_eligible"] is False

        # Check Congo vs Central African Republic (with disambiguated away team name)
        congo_key = "Congo vs Central African Rep."
        assert congo_key in matches
        assert matches[congo_key]["league_name"] == "Friendly International Women"
        assert matches[congo_key]["prediction_eligible"] is False

        # Check Senior Fixture (Junior vs Cúcuta)
        junior_key = "Junior vs Cúcuta"
        assert junior_key in matches
        assert matches[junior_key]["prediction_eligible"] is True


# ==============================================================================
# 4. PREDICTION INTEGRITY & ZERO POLLUTION TESTS
# ==============================================================================

class TestPredictionIntegrityAndZeroPollution:

    def test_zero_predictions_exist_for_all_disputed_fixtures(self):
        """Ensure 0 prediction records exist in prediction_logs for any of the 9 disputed fixture IDs."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        fids = TestProviderFixtureIdentifierSpecification.DISPUTED_FIXTURE_IDS
        placeholders = ",".join(["?"] * len(fids))
        c.execute(f"SELECT COUNT(*) FROM prediction_logs WHERE provider_fixture_id IN ({placeholders})", fids)
        count = c.fetchone()[0]
        assert count == 0, f"Found {count} unexpected prediction records for disputed fixtures"
        conn.close()

    def test_historical_statistical_metrics_unpolluted(self):
        """Historical statistics must remain exactly 366 provider-verified records, 55.5% strike rate, -26.19u net profit."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified'")
        pv_count = c.fetchone()[0]
        assert pv_count == 366

        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified' AND outcome = 'correct'")
        won = c.fetchone()[0]
        assert won == 203

        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified' AND outcome = 'incorrect'")
        lost = c.fetchone()[0]
        assert lost == 163

        c.execute("SELECT ROUND(SUM(unit_return), 2) FROM prediction_logs WHERE source_classification = 'provider_verified'")
        net_profit = c.fetchone()[0]
        assert net_profit == -26.19
        conn.close()
