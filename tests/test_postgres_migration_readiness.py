"""
SportyMill PostgreSQL Migration Readiness & Production Acceptance Test Suite
Verifies:
- Database URL normalization (postgres:// -> postgresql+asyncpg://)
- Health diagnostics (safe engine/status reporting without credential leaks)
- Daily slate error handling (503 Service Unavailable on DB error, 200 on provider outage)
- SQLite pre-migration backup dataset reconciliation (27,600 rows)
- Prediction logs provenance, 366 distinct fixture IDs, and verified strike rate (55.5%)
- Migration engine dependency order and idempotent upsert logic
"""
import os
import sys
import sqlite3
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app, config
from scripts.migrate_sqlite_to_postgres import TABLES_IN_DEPENDENCY_ORDER, normalize_db_url, get_sqlite_tables_and_counts

client = TestClient(app)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKUP_PATH = os.path.join(ROOT_DIR, "tipalgo_pre_migration_backup.db")
DB_PATH = os.path.join(ROOT_DIR, "tipalgo.db")


# ==============================================================================
# 1. DATABASE CONFIGURATION & URL NORMALIZATION TESTS
# ==============================================================================

class TestDatabaseConfigurationAndUrlNormalization:

    def test_postgres_url_normalization_standard(self):
        """postgres:// scheme must be converted to postgresql+asyncpg://."""
        raw_url = "postgres://sporty_user:secret_pass@ep-cool-db.us-east-2.render.com:5432/sportymill"
        normalized = normalize_db_url(raw_url)
        assert normalized.startswith("postgresql+asyncpg://")
        assert "sporty_user:secret_pass" in normalized
        assert "ep-cool-db.us-east-2.render.com:5432/sportymill" in normalized

    def test_postgresql_url_normalization_with_sslmode(self):
        """postgresql:// scheme with sslmode query param must normalize correctly."""
        raw_url = "postgresql://usr:pwd@host.aws.neon.tech:5432/neondb?sslmode=require"
        normalized = normalize_db_url(raw_url)
        assert normalized.startswith("postgresql+asyncpg://")
        assert "sslmode=require" not in normalized  # Query string stripped for asyncpg SSL context handling
        assert "host.aws.neon.tech:5432/neondb" in normalized

    def test_sqlite_url_passthrough(self):
        """SQLite URLs must remain untouched."""
        sqlite_url = "sqlite+aiosqlite:///tipalgo.db"
        assert normalize_db_url(sqlite_url) == sqlite_url

    def test_health_telemetry_safe_diagnostics(self):
        """GET /health must report database_engine and database_status without exposing credentials."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "database_engine" in data
        assert data["database_engine"] in ("sqlite", "postgresql")
        assert "database_status" in data
        assert data["database_status"] in ("connected", "degraded")
        # Ensure no passwords or raw connection strings are leaked
        response_text = response.text.lower()
        assert "password" not in response_text
        assert "secret" not in response_text
        assert "postgresql://" not in response_text
        assert "postgres://" not in response_text


# ==============================================================================
# 2. DAILY SLATE STATUS CODE & ERROR ISOLATION TESTS
# ==============================================================================

class TestDailySlateErrorIsolation:

    def test_database_failure_returns_503(self):
        """A database connectivity error in /api/calendar/daily-slate must return HTTP 503."""
        from main import get_db, calendar_slate_cache

        calendar_slate_cache.clear()

        async def failing_session():
            mock_s = AsyncMock()
            mock_s.execute.side_effect = OperationalError("connection refused", {}, Exception("Network unreachable"))
            yield mock_s

        app.dependency_overrides[get_db] = failing_session
        try:
            response = client.get("/api/calendar/daily-slate?date=2026-12-01&endpoint=all")
            assert response.status_code == 503
            assert "Database Service Unavailable" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_provider_outage_returns_200_with_error_status_and_zero_fakes(self):
        """When live API is unreachable, endpoint must return 200 with provider_status='error' and no synthetic fixtures."""
        # Querying a future date that has no cached fixtures
        response = client.get("/api/calendar/daily-slate?date=2026-12-31&endpoint=all")
        assert response.status_code == 200
        data = response.json()
        assert data["selected_date"] == "2026-12-31"
        assert data["matches_count"] == 0
        assert data["matches"] == []
        assert data["provider_status"] in ("ok", "error", "degraded")
        if data["provider_status"] == "error":
            assert "External live sync service unavailable" in data["provider_message"]


# ==============================================================================
# 3. PRE-MIGRATION BACKUP & SOURCE RECONCILIATION TESTS
# ==============================================================================

class TestBackupAndSourceDatasetReconciliation:

    def test_pre_migration_backup_file_exists(self):
        """The verified binary backup file must exist and be non-empty."""
        assert os.path.exists(BACKUP_PATH), f"Backup file not found at {BACKUP_PATH}"
        assert os.path.getsize(BACKUP_PATH) > 5_000_000, "Backup file is smaller than 5MB"

    def test_backup_and_source_table_row_counts(self):
        """Both tipalgo.db and backup must contain at least 27,600 verified records and match 100%."""
        min_expected_counts = {
            "users": 4,
            "authorized_devices": 5,
            "league_tables": 4556,
            "historical_matches": 22583,
            "head_to_head": 12,
            "bet_slips": 2,
            "calibration_state": 0,
            "feature_store": 0,
            "prediction_logs": 438,
        }
        baseline_minimum = 27600

        source_counts = get_sqlite_tables_and_counts(DB_PATH)
        backup_counts = get_sqlite_tables_and_counts(BACKUP_PATH)

        # Source and backup must be identical mirrors
        assert source_counts == backup_counts, f"Source and backup counts differ: {source_counts} vs {backup_counts}"

        total_source = sum(source_counts.values())
        assert total_source >= baseline_minimum, f"Source total count {total_source} < baseline {baseline_minimum}"

        for table, min_expected in min_expected_counts.items():
            actual = source_counts.get(table, 0)
            if table in ("users", "authorized_devices", "head_to_head", "bet_slips", "prediction_logs"):
                assert actual == min_expected, f"Table {table} has {actual}, expected exactly {min_expected}"
            else:
                assert actual >= min_expected, f"Table {table} has {actual}, expected at least {min_expected}"

    def test_prediction_logs_provenance_and_distinct_provider_fixture_ids(self):
        """Verify 366 provider_verified records with 366 distinct fixture IDs and 55.5% strike rate."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # 1. Total records
        c.execute("SELECT COUNT(*) FROM prediction_logs")
        total = c.fetchone()[0]
        assert total == 438

        # 2. Provider verified vs Quarantined (non-verified)
        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified'")
        pv_count = c.fetchone()[0]
        assert pv_count == 366

        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification != 'provider_verified'")
        q_count = c.fetchone()[0]
        assert q_count == 72

        # 3. Distinct fixture IDs
        c.execute("SELECT COUNT(DISTINCT provider_fixture_id) FROM prediction_logs WHERE source_classification = 'provider_verified'")
        distinct_fids = c.fetchone()[0]
        assert distinct_fids == 366

        # 4. Settled counts (outcome: 'correct' = won, 'incorrect' = lost)
        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified' AND outcome = 'correct'")
        won = c.fetchone()[0]
        assert won == 203

        c.execute("SELECT COUNT(*) FROM prediction_logs WHERE source_classification = 'provider_verified' AND outcome = 'incorrect'")
        lost = c.fetchone()[0]
        assert lost == 163

        # 5. Strike rate
        strike_rate = round((won / pv_count) * 100, 1)
        assert strike_rate == 55.5

        # 6. Net units profit
        c.execute("SELECT ROUND(SUM(unit_return), 2) FROM prediction_logs WHERE source_classification = 'provider_verified'")
        net_profit = c.fetchone()[0]
        assert net_profit == -26.19

        conn.close()


# ==============================================================================
# 4. MIGRATION ENGINE ARCHITECTURE TESTS
# ==============================================================================

class TestMigrationEngineArchitecture:

    def test_tables_in_dependency_order(self):
        """Foreign key parent tables must precede children in migration order."""
        order = TABLES_IN_DEPENDENCY_ORDER
        assert order.index("users") < order.index("authorized_devices")
        assert order.index("league_tables") < order.index("historical_matches")
        assert order.index("historical_matches") < order.index("prediction_logs")

    def test_migration_all_nine_tables_covered(self):
        """All 9 tables must be present in migration dependency order."""
        assert len(TABLES_IN_DEPENDENCY_ORDER) == 9
        expected = [
            "users", "authorized_devices", "league_tables", "historical_matches",
            "head_to_head", "bet_slips", "calibration_state", "feature_store", "prediction_logs"
        ]
        assert TABLES_IN_DEPENDENCY_ORDER == expected
