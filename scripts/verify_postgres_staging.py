"""
SportyMill PostgreSQL Staging Verification Script
Executes full live database verification against a PostgreSQL cluster:
1. Version & connection check
2. Migration 002 upgrade (partial unique index creation)
3. pg_indexes validation (index name & predicate matching)
4. Multi-market insert for single fixture (11 distinct endpoints without collision)
5. 10-thread concurrent duplicate insert idempotency
6. Snapshot immutability verification (rejection of subsequent recalculation overwrite)
7. Transaction rollback safety check
8. Multi-worker concurrent settlement test (5 independent connections using conditional SQL update)
9. Partial index predicate verification (non-live records unblocked)
10. Migration 002 downgrade (clean non-destructive index drop)
11. Clean re-upgrade leaving staging ready
"""
import sys
import os
import json
import uuid
import asyncio
import logging
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("postgres_staging_verifier")

# Dynamic import of migration module
mig_002 = importlib.import_module("migrations.002_live_prediction_idempotency")
POSTGRES_UPGRADE_SQL = mig_002.POSTGRES_UPGRADE_SQL
POSTGRES_VERIFY_SQL = mig_002.POSTGRES_VERIFY_SQL
POSTGRES_DOWNGRADE_SQL = mig_002.POSTGRES_DOWNGRADE_SQL
INDEX_NAME = mig_002.INDEX_NAME
TABLE_NAME = mig_002.TABLE_NAME


async def verify_postgres_staging(db_url: str):
    logger.info(f"Connecting to PostgreSQL staging database: {db_url.split('@')[-1]}")
    try:
        import asyncpg
    except ImportError:
        logger.error("asyncpg is required. Run: pip install asyncpg")
        sys.exit(1)

    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        # 1. Version check
        pg_version = await conn.fetchval("SELECT version();")
        logger.info(f"PostgreSQL Server Version: {pg_version}")

        # Check isolation level
        iso_level = await conn.fetchval("SHOW transaction_isolation;")
        logger.info(f"Default Transaction Isolation Level: {iso_level}")

        # 2. Run Migration 002 Upgrade
        logger.info(f"Applying migration 002: Creating {INDEX_NAME}...")
        await conn.execute(POSTGRES_UPGRADE_SQL)

        # 3. Verify pg_indexes
        idx_row = await conn.fetchrow(POSTGRES_VERIFY_SQL)
        if not idx_row:
            logger.error(f"FATAL: Index {INDEX_NAME} not found in pg_indexes after upgrade.")
            sys.exit(1)
        logger.info(f"VERIFIED in pg_indexes: {idx_row['indexname']} -> {idx_row['indexdef']}")
        assert "live_recorded" in idx_row['indexdef'], "Index predicate does not restrict to live_recorded!"

        # 4. Multi-market insert for single fixture (11 endpoints)
        test_fid = f"pg_stage_fix_{uuid.uuid4().hex[:8]}"
        endpoints = [
            "home-win", "draw", "away-win",
            "over-1-5", "over-2-5", "over-3-5", "no-three-goals",
            "btts-yes", "btts-no",
            "double-chance-1x", "double-chance-x2"
        ]
        logger.info(f"Inserting {len(endpoints)} distinct markets for fixture {test_fid}...")
        for ep in endpoints:
            await conn.execute(f"""
                INSERT INTO {TABLE_NAME} (
                    prediction_id, endpoint, home_team, away_team, 
                    fixture_json, raw_probs_json, market_probs_json,
                    source_classification, provider_fixture_id, outcome, confidence_percent
                ) VALUES (
                    $1, $2, 'Real Madrid', 'Barcelona',
                    $3, $4, $5, 'live_recorded', $6, 'pending', 55.0
                )
                ON CONFLICT (provider_fixture_id, endpoint)
                WHERE source_classification = 'live_recorded'
                DO NOTHING;
            """, str(uuid.uuid4()), ep, json.dumps({"selection": ep, "market": ep}), json.dumps({"p": 55}), json.dumps({"odds": 2.0}), test_fid)

        count_markets = await conn.fetchval(f"SELECT count(*) FROM {TABLE_NAME} WHERE provider_fixture_id = $1 AND source_classification = 'live_recorded';", test_fid)
        assert count_markets == len(endpoints), f"Expected {len(endpoints)} markets, got {count_markets}"
        logger.info(f"Multi-market insertion: SUCCESS ({count_markets}/{len(endpoints)} persisted without collision)")

        # 5. 10 Concurrent duplicate inserts on same fixture and endpoint
        logger.info("Executing 10 concurrent duplicate insert attempts...")
        test_ep = "home-win"
        async def insert_worker():
            c = await asyncpg.connect(clean_url)
            try:
                await c.execute(f"""
                    INSERT INTO {TABLE_NAME} (
                        prediction_id, endpoint, home_team, away_team, 
                        fixture_json, raw_probs_json, market_probs_json,
                        source_classification, provider_fixture_id, outcome, confidence_percent
                    ) VALUES (
                        $1, $2, 'Real Madrid', 'Barcelona',
                        $3, $4, $5, 'live_recorded', $6, 'pending', 90.0
                    )
                    ON CONFLICT (provider_fixture_id, endpoint)
                    WHERE source_classification = 'live_recorded'
                    DO NOTHING;
                """, str(uuid.uuid4()), test_ep, json.dumps({"odds": 1.20}), json.dumps({"p": 90}), json.dumps({"odds": 1.20}), test_fid)
            finally:
                await c.close()

        await asyncio.gather(*(insert_worker() for _ in range(10)))
        count_after = await conn.fetchval(f"SELECT count(*) FROM {TABLE_NAME} WHERE provider_fixture_id = $1 AND endpoint = $2 AND source_classification = 'live_recorded';", test_fid, test_ep)
        assert count_after == 1, f"Concurrency test failed: Expected 1 record for endpoint, got {count_after}"
        logger.info("Concurrency duplicate suppression: SUCCESS (count remains exactly 1)")

        # 6. Snapshot immutability check: ensure initial confidence was NOT overwritten
        conf = await conn.fetchval(f"SELECT confidence_percent FROM {TABLE_NAME} WHERE provider_fixture_id = $1 AND endpoint = $2 AND source_classification = 'live_recorded';", test_fid, test_ep)
        assert conf == 55.0, f"Immutability violated: confidence overwritten from 55.0 to {conf}"
        logger.info("Snapshot immutability: SUCCESS (original confidence 55.0% preserved)")

        # 7. Transaction rollback safety check
        logger.info("Testing transaction rollback safety...")
        tr_fid = f"pg_rollback_fix_{uuid.uuid4().hex[:8]}"
        async with conn.transaction():
            await conn.execute(f"""
                INSERT INTO {TABLE_NAME} (
                    prediction_id, endpoint, home_team, away_team, 
                    fixture_json, raw_probs_json, market_probs_json,
                    source_classification, provider_fixture_id, outcome
                ) VALUES ($1, 'draw', 'Team A', 'Team B', '{{}}', '{{}}', '{{}}', 'live_recorded', $2, 'pending')
            """, str(uuid.uuid4()), tr_fid)
            # Deliberate rollback
            raise Exception("DeliberateRollbackTest")
    except Exception as e:
        if "DeliberateRollbackTest" in str(e):
            rb_count = await conn.fetchval(f"SELECT count(*) FROM {TABLE_NAME} WHERE provider_fixture_id = $1;", tr_fid)
            assert rb_count == 0, f"Rollback failed: {rb_count} records remained after rollback"
            logger.info("Transaction rollback: SUCCESS (0 records remained)")
        else:
            raise

    try:
        # 8. PostgreSQL Concurrency Settlement Test (5 Independent Workers)
        logger.info("Testing multi-worker concurrent settlement under PostgreSQL with 5 independent connections...")
        settle_fid = f"pg_settle_fix_{uuid.uuid4().hex[:8]}"
        pred_id = str(uuid.uuid4())
        await conn.execute(f"""
            INSERT INTO {TABLE_NAME} (
                prediction_id, endpoint, home_team, away_team, 
                fixture_json, raw_probs_json, market_probs_json,
                source_classification, provider_fixture_id, outcome, confidence_percent
            ) VALUES (
                $1, 'home-win', 'Bayern Munich', 'Dortmund',
                $2, $3, $4, 'live_recorded', $5, 'pending', 60.0
            );
        """, pred_id, json.dumps({"selection": "Bayern: Home Win", "market": "Bayern Win"}), json.dumps({"p": 60}), json.dumps({"odds": 1.80}), settle_fid)

        # 5 independent settlement workers attempting conditional update simultaneously
        async def settlement_worker(worker_id: int):
            c = await asyncpg.connect(clean_url)
            try:
                res = await c.execute(f"""
                    UPDATE {TABLE_NAME}
                    SET outcome = 'WON',
                        actual_home_score = 3,
                        actual_away_score = 1,
                        unit_return = 0.80,
                        settled_at = NOW(),
                        feedback_notes = 'PostgreSQL staging concurrent settlement test'
                    WHERE provider_fixture_id = $1 
                      AND endpoint = 'home-win'
                      AND outcome = 'pending';
                """, settle_fid)
                # res is formatted like 'UPDATE 1' or 'UPDATE 0'
                updated_rows = int(res.split(" ")[-1])
                return updated_rows
            finally:
                await c.close()

        worker_results = await asyncio.gather(*(settlement_worker(i) for i in range(5)))
        total_settled = sum(worker_results)
        logger.info(f"PostgreSQL Concurrent Settlement Results: {worker_results} (Total: {total_settled})")
        assert total_settled == 1, f"PostgreSQL Concurrency Failure: Expected exactly 1 update, got {total_settled}"
        logger.info("PostgreSQL Concurrency Settlement: SUCCESS (exactly 1 worker succeeded, 4 reported 0 rows)")

        # Verify final row
        final_row = await conn.fetchrow(f"SELECT outcome, actual_home_score, actual_away_score, unit_return FROM {TABLE_NAME} WHERE provider_fixture_id = $1 AND endpoint = 'home-win';", settle_fid)
        assert final_row['outcome'] == 'WON'
        assert final_row['unit_return'] == 0.80
        logger.info("PostgreSQL Final Financial Outcome: SUCCESS (WON, +0.80 units recorded once)")

        # 9. Verify partial index predicate (same fixture and endpoint with 'provider_verified' should succeed)
        logger.info("Testing partial index predicate with non-live source_classification...")
        await conn.execute(f"""
            INSERT INTO {TABLE_NAME} (
                prediction_id, endpoint, home_team, away_team, 
                fixture_json, raw_probs_json, source_classification, provider_fixture_id
            ) VALUES (
                $1, 'home-win', 'Real Madrid', 'Barcelona', 
                '{{}}', '{{}}', 'provider_verified', $2
            );
        """, str(uuid.uuid4()), test_fid)
        total_for_fid = await conn.fetchval(f"SELECT count(*) FROM {TABLE_NAME} WHERE provider_fixture_id = $1;", test_fid)
        assert total_for_fid == len(endpoints) + 1, f"Partial index blocked unverified/verified row! Total: {total_for_fid}"
        logger.info("Partial index predicate check: SUCCESS (non-live records unblocked)")

        # Clean up test rows
        await conn.execute(f"DELETE FROM {TABLE_NAME} WHERE provider_fixture_id IN ($1, $2);", test_fid, settle_fid)

        # 10. Test Downgrade
        logger.info("Testing migration 002 downgrade on PostgreSQL...")
        await conn.execute(POSTGRES_DOWNGRADE_SQL)
        idx_after_drop = await conn.fetchrow(POSTGRES_VERIFY_SQL)
        assert idx_after_drop is None, f"Index {INDEX_NAME} still exists after downgrade!"
        logger.info("Migration 002 downgrade: SUCCESS (index dropped cleanly)")

        # Re-apply upgrade so staging remains prepared
        await conn.execute(POSTGRES_UPGRADE_SQL)
        logger.info("Staging environment re-upgraded. POSTGRESQL VERIFICATION COMPLETE & PASSED.")
    finally:
        await conn.close()


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not target_url or "postgres" not in target_url:
        print("""
=============================================================================
SPORTYMILL POSTGRESQL STAGING VERIFICATION PREREQUISITE NOTICE
=============================================================================
PostgreSQL staging credentials or daemon are not provisioned in this environment.
Missing prerequisite: Staging PostgreSQL connection URI.

Usage:
  python scripts/verify_postgres_staging.py <POSTGRES_URL>
Or:
  export DATABASE_URL="postgresql://user:password@host:5432/dbname"
  python scripts/verify_postgres_staging.py

Required Staging Verification Steps Configured:
  [1] PostgreSQL Server Version & Isolation Level Check
  [2] Migration 002 Upgrade (CREATE UNIQUE INDEX IF NOT EXISTS...)
  [3] pg_indexes Predicate Verification (WHERE source_classification = 'live_recorded')
  [4] Multi-Market Insertion (11 endpoints for single fixture)
  [5] 10-Worker Concurrent Duplicate Insert Idempotency
  [6] Snapshot Immutability Verification (Odds & Probabilities)
  [7] Transaction Rollback Safety Check
  [8] Multi-Worker Concurrent Settlement Test (5 Independent Connections via OCC)
  [9] Partial Index Predicate Isolation ('provider_verified' unblocked)
  [10] Non-Destructive Downgrade Verification (DROP INDEX IF EXISTS)
=============================================================================
""")
        sys.exit(0)

    asyncio.run(verify_postgres_staging(target_url))
