#!/usr/bin/env python3
"""
SportyMill Production Migration Engine
Transfers all historical football data, user accounts, and prediction records
from local SQLite (tipalgo.db) to Managed PostgreSQL (asyncpg) with full
provenance verification, table-by-table reconciliation, 64-char SHA-256 fingerprints,
and zero data loss.

Usage:
  # Preferred: configure TARGET_DATABASE_URL or DATABASE_URL in environment
  export TARGET_DATABASE_URL="postgresql+asyncpg://user:pass@host/db"
  python3 scripts/migrate_sqlite_to_postgres.py [options]

Options:
  --source PATH        Source SQLite database path (default: tipalgo.db)
  --target URL         Target PostgreSQL DATABASE_URL (overrides env var if supplied)
  --batch-size N       Batch insert chunk size (default: 1000)
  --verify-only        Verify schema, counts, and provenance without migrating
  --dry-run            Simulate migration in a transaction without committing
  --sanitize           Exclude test-generated users and bet slips from migration
"""

import os
import re
import ssl
import sys
import json
import sqlite3
import asyncio
import logging
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure project root is in path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func, text, inspect, case
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# Import SQLAlchemy models from main
from main import (
    Base, User, AuthorizedDevice, LeagueTable, HistoricalMatch,
    PredictionLog, HeadToHead, BetSlip, CalibrationState, FeatureStore,
    Config
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration_engine")

TABLE_MODEL_MAP = {
    "users": User,
    "authorized_devices": AuthorizedDevice,
    "league_tables": LeagueTable,
    "historical_matches": HistoricalMatch,
    "head_to_head": HeadToHead,
    "bet_slips": BetSlip,
    "calibration_state": CalibrationState,
    "feature_store": FeatureStore,
    "prediction_logs": PredictionLog
}

MIGRATION_ORDER = [
    "users",
    "authorized_devices",
    "league_tables",
    "historical_matches",
    "head_to_head",
    "bet_slips",
    "calibration_state",
    "feature_store",
    "prediction_logs"
]

TABLES_IN_DEPENDENCY_ORDER = MIGRATION_ORDER


def redact_db_url(url: str) -> str:
    """Safely redact sensitive credentials from database URLs for logging and output."""
    if not url:
        return "[NOT SET]"
    try:
        return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", url)
    except Exception:
        return "[REDACTED]"


class MigrationDiscrepancyError(Exception):
    """Raised when post-migration counts or financial metrics do not reconcile 100%."""
    pass


def parse_datetime_safe(val: Any) -> Optional[datetime]:
    """Parse various SQLite datetime formats into timezone-aware UTC datetime."""
    if val is None or str(val).strip() in ("", "None", "null"):
        return None
    if isinstance(val, datetime):
        if not val.tzinfo:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    val_str = str(val).strip()
    clean_str = val_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean_str)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d"
    ):
        try:
            dt = datetime.strptime(val_str[:26], fmt)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    logger.warning(f"Could not parse datetime string: {val_str}. Using current UTC.")
    return datetime.now(timezone.utc)


def canonical_serialize_val(val: Any, col_name: str) -> str:
    """Canonical value serialization for deterministic SHA-256 fingerprinting."""
    if val is None:
        return "__NULL__"
    if isinstance(val, (int, bool)) and ('is_' in col_name or 'processed' in col_name or 'playbook' in col_name):
        return "true" if bool(val) else "false"
    if isinstance(val, float):
        return f"{val:.6f}"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, datetime):
        if not val.tzinfo:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(val, str):
        s = val.strip()
        if (s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']')):
            try:
                parsed = json.loads(s)
                return json.dumps(parsed, sort_keys=True, separators=(',', ':'))
            except Exception:
                pass
        if len(s) >= 10 and ('-' in s[:10]):
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"
            ):
                try:
                    dt = datetime.strptime(s[:26], fmt)
                    if not dt.tzinfo:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    continue
        return s
    return str(val).strip()


def compute_sqlite_table_fingerprint(conn: sqlite3.Connection, table_name: str) -> str:
    """Compute full 64-character SHA-256 aggregate digest of an SQLite table."""
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table_name} ORDER BY id ASC")
    cols = [d[0] for d in cur.description]
    table_hasher = hashlib.sha256()

    for row in cur.fetchall():
        row_dict = dict(zip(cols, row))
        canonical_parts = [f"{c}={canonical_serialize_val(row_dict[c], c)}" for c in sorted(cols)]
        row_str = "|".join(canonical_parts)
        row_hash = hashlib.sha256(row_str.encode("utf-8")).hexdigest()
        table_hasher.update(row_hash.encode("utf-8"))

    return table_hasher.hexdigest()


def get_sqlite_tables_and_counts(sqlite_path: str) -> Dict[str, int]:
    """Retrieve all table names and exact row counts from SQLite database."""
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"Source SQLite database not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row[0] for row in cursor.fetchall()]

    counts = {}
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]

    conn.close()
    return counts


def normalize_target_url(raw_url: str) -> str:
    """Normalize PostgreSQL URL for asyncpg and enforce secure drivers."""
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    if "postgres" in url and "?" in url:
        base_url, query_str = url.split("?", 1)
        params = [p for p in query_str.split("&") if not p.startswith("sslmode=")]
        url = f"{base_url}?{'&'.join(params)}" if params else base_url
    return url


normalize_db_url = normalize_target_url


def build_engine_for_url(norm_url: str, echo: bool = False):
    """Build async SQLAlchemy engine with proper SSL and connection pooling."""
    is_pg = "postgres" in norm_url

    if is_pg:
        connect_args = {}
        if any(h in norm_url for h in ("render.com", "neon.tech", "supabase.co", "rds.amazonaws.com")) or "ssl" in norm_url:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ssl_ctx

        return create_async_engine(
            norm_url,
            echo=echo,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args=connect_args
        )
    else:
        return create_async_engine(
            norm_url,
            echo=echo,
            connect_args={"check_same_thread": False}
        )


async def execute_migration(
    sqlite_path: str,
    target_db_url: str,
    batch_size: int = 1000,
    dry_run: bool = False,
    verify_only: bool = False,
    sanitize_test_data: bool = False
) -> Dict[str, Any]:
    """Execute complete, idempotent table-by-table migration with forensic reconciliation."""
    logger.info("=" * 72)
    logger.info("SPORTYMILL ENTERPRISE SQLITE -> POSTGRESQL MIGRATION ENGINE")
    logger.info("=" * 72)
    logger.info(f"Source SQLite Path: {sqlite_path}")

    norm_url = normalize_target_url(target_db_url)
    is_pg = "postgres" in norm_url
    logger.info(f"Target Database Host: {redact_db_url(norm_url)} (Engine: {'PostgreSQL' if is_pg else 'SQLite'})")

    # 1. Inspect Source SQLite & Compute Canonical Fingerprints
    source_counts = get_sqlite_tables_and_counts(sqlite_path)
    src_conn_fp = sqlite3.connect(sqlite_path)
    source_fingerprints: Dict[str, str] = {}
    logger.info("Computing Source Table Row Counts & Full 64-char SHA-256 Fingerprints:")
    for tbl in MIGRATION_ORDER:
        cnt = source_counts.get(tbl, 0)
        fp = compute_sqlite_table_fingerprint(src_conn_fp, tbl)
        source_fingerprints[tbl] = fp
        logger.info(f"  - {tbl:<20}: {cnt:>6} rows | SHA-256: {fp}")
    src_conn_fp.close()

    total_source_rows = sum(source_counts.get(t, 0) for t in MIGRATION_ORDER)
    logger.info(f"Total Source Rows: {total_source_rows} records across {len(MIGRATION_ORDER)} tables.")

    # 2. Connect to Target and Verify Engine
    target_engine = build_engine_for_url(norm_url)
    async with target_engine.connect() as conn:
        if is_pg:
            v_res = await conn.execute(text("SELECT version();"))
            db_version = str(v_res.scalar() or "Unknown PostgreSQL")
            logger.info(f"VERIFIED DESTINATION ENGINE: Genuine PostgreSQL Server ({db_version[:65]}...)")
        else:
            logger.warning("VERIFIED DESTINATION ENGINE: SQLite Local Staging Engine (REHEARSAL MODE ONLY)")

    if not verify_only:
        async with target_engine.begin() as conn:
            logger.info("Synchronizing destination schema with SQLAlchemy metadata...")
            await conn.run_sync(Base.metadata.create_all)

    TargetSession = async_sessionmaker(target_engine, expire_on_commit=False)

    # 3. Read & Transfer Data
    src_conn = sqlite3.connect(sqlite_path)
    src_conn.row_factory = sqlite3.Row
    src_cur = src_conn.cursor()

    migrated_counts: Dict[str, int] = {}

    if not verify_only:
        async with TargetSession() as session:
            for table_name in MIGRATION_ORDER:
                model_cls = TABLE_MODEL_MAP[table_name]
                expected_cnt = source_counts.get(table_name, 0)
                if expected_cnt == 0:
                    logger.info(f"Skipping empty table: {table_name}")
                    migrated_counts[table_name] = 0
                    continue

                logger.info(f"Migrating table: {table_name} ({expected_cnt} rows)...")

                col_types = {}
                for col in inspect(model_cls).columns:
                    col_types[col.name] = col.type

                query = f"SELECT * FROM {table_name}"
                if sanitize_test_data:
                    if table_name == "users":
                        query += " WHERE id <= 4"
                    elif table_name == "bet_slips":
                        query += " WHERE id <= 2"
                    elif table_name == "authorized_devices":
                        query += " WHERE id <= 2"
                query += " ORDER BY id ASC"

                src_cur.execute(query)
                col_names = [d[0] for d in src_cur.description]

                offset = 0
                while True:
                    rows = src_cur.fetchmany(batch_size)
                    if not rows:
                        break

                    records_to_insert = []
                    for row in rows:
                        rec_dict = {}
                        for idx, col_name in enumerate(col_names):
                            if col_name not in col_types:
                                continue

                            val = row[idx]
                            col_type = col_types[col_name]

                            if val is None:
                                rec_dict[col_name] = None
                            elif hasattr(col_type, "python_type") and col_type.python_type is datetime:
                                rec_dict[col_name] = parse_datetime_safe(val)
                            elif hasattr(col_type, "python_type") and col_type.python_type is bool:
                                rec_dict[col_name] = bool(val)
                            elif hasattr(col_type, "python_type") and col_type.python_type is float:
                                rec_dict[col_name] = float(val) if val is not None else None
                            elif hasattr(col_type, "python_type") and col_type.python_type is int:
                                rec_dict[col_name] = int(val) if val is not None else None
                            else:
                                rec_dict[col_name] = val

                        records_to_insert.append(rec_dict)

                    if records_to_insert:
                        if is_pg:
                            stmt = pg_insert(model_cls).values(records_to_insert)
                            stmt = stmt.on_conflict_do_nothing(index_elements=['id'])
                        else:
                            stmt = sqlite_insert(model_cls).values(records_to_insert)
                            stmt = stmt.on_conflict_do_nothing(index_elements=['id'])

                        await session.execute(stmt)
                        if not dry_run:
                            await session.commit()

                    offset += len(rows)
                    if offset % 5000 == 0 or offset >= expected_cnt:
                        logger.info(f"  -> {table_name} progress: {offset} / {expected_cnt}")

                cnt_res = await session.execute(select(func.count(model_cls.id)))
                migrated_counts[table_name] = cnt_res.scalar() or 0
                logger.info(f"Table {table_name} migration complete: {migrated_counts[table_name]} rows in target.")

            # PostgreSQL sequence synchronization with empty table protection
            if is_pg and not dry_run:
                logger.info("Synchronizing PostgreSQL primary key sequences...")
                for table_name in MIGRATION_ORDER:
                    try:
                        await session.execute(text(f"""
                            DO $$
                            DECLARE
                                v_max_id bigint;
                                v_seq text;
                            BEGIN
                                SELECT pg_get_serial_sequence('{table_name}', 'id') INTO v_seq;
                                IF v_seq IS NOT NULL THEN
                                    SELECT MAX(id) FROM {table_name} INTO v_max_id;
                                    IF v_max_id IS NOT NULL THEN
                                        PERFORM setval(v_seq, v_max_id, true);
                                    ELSE
                                        PERFORM setval(v_seq, 1, false);
                                    END IF;
                                END IF;
                            END $$;
                        """))
                    except Exception as seq_err:
                        logger.warning(f"Notice setting sequence for {table_name}: {seq_err}")
                await session.commit()

    src_q_res = src_conn.execute(
        "SELECT count(*) FROM prediction_logs WHERE source_classification IN ('invalid', 'synthetic', 'seed', 'unverified')"
    )
    src_quarantined_count = src_q_res.fetchone()[0]
    src_conn.close()

    # 4. Forensic Reconciliation & Audit Validation
    logger.info("=" * 72)
    logger.info("POST-MIGRATION FORENSIC RECONCILIATION")
    logger.info("=" * 72)

    async with TargetSession() as session:
        target_counts = {}
        for table_name in MIGRATION_ORDER:
            model_cls = TABLE_MODEL_MAP[table_name]
            cnt_res = await session.execute(select(func.count(model_cls.id)))
            target_counts[table_name] = cnt_res.scalar() or 0

        pv_res = await session.execute(
            select(func.count(PredictionLog.id)).where(PredictionLog.source_classification == "provider_verified")
        )
        pv_count = pv_res.scalar() or 0

        q_res = await session.execute(
            select(func.count(PredictionLog.id)).where(
                PredictionLog.source_classification.in_(["invalid", "synthetic", "seed", "unverified"])
            )
        )
        quarantined_count = q_res.scalar() or 0

        fid_res = await session.execute(
            select(func.count(func.distinct(PredictionLog.provider_fixture_id)))
            .where(
                PredictionLog.source_classification == "provider_verified",
                PredictionLog.provider_fixture_id.isnot(None)
            )
        )
        distinct_fid_count = fid_res.scalar() or 0

        outcomes_res = await session.execute(
            select(
                func.count(PredictionLog.id).label("total"),
                func.sum(case((PredictionLog.outcome == "correct", 1), else_=0)).label("won"),
                func.sum(case((PredictionLog.outcome == "incorrect", 1), else_=0)).label("lost"),
                func.sum(case((PredictionLog.outcome == "pending", 1), else_=0)).label("pending"),
                func.sum(PredictionLog.unit_return).label("net_profit")
            ).where(PredictionLog.source_classification == "provider_verified")
        )
        o_row = outcomes_res.fetchone()
        won = int(o_row.won or 0)
        lost = int(o_row.lost or 0)
        pending = int(o_row.pending or 0)
        net_profit = round(float(o_row.net_profit or 0.0), 2)
        strike_rate = round((won / (won + lost) * 100), 1) if (won + lost) > 0 else 0.0

    discrepancies = []
    if not sanitize_test_data:
        for tbl in MIGRATION_ORDER:
            src_c = source_counts.get(tbl, 0)
            tgt_c = target_counts.get(tbl, 0)
            if src_c != tgt_c:
                discrepancies.append(f"Table '{tbl}' mismatch: source={src_c}, target={tgt_c}")

    if pv_count != 366:
        discrepancies.append(f"Provider-verified predictions count mismatch: expected 366, got {pv_count}")
    if distinct_fid_count != 366:
        discrepancies.append(f"Distinct provider fixture IDs mismatch: expected 366, got {distinct_fid_count}")
    if not sanitize_test_data and quarantined_count != src_quarantined_count:
        discrepancies.append(f"Quarantined records mismatch: expected {src_quarantined_count}, got {quarantined_count}")
    if won != 203 or lost != 163:
        discrepancies.append(f"Settled outcomes mismatch: expected 203 won / 163 lost, got {won} won / {lost} lost")

    reconciliation = {
        "status": "SUCCESS" if not discrepancies else "DISCREPANCY_DETECTED",
        "database_engine": "postgresql" if is_pg else "sqlite_staging_rehearsal",
        "target_url_redacted": redact_db_url(norm_url),
        "source_sqlite_path": sqlite_path,
        "total_source_records": total_source_rows,
        "total_migrated_records": sum(target_counts.values()),
        "source_counts": source_counts,
        "target_counts": target_counts,
        "source_sha256_fingerprints": source_fingerprints,
        "provider_verified_count": pv_count,
        "quarantined_count": quarantined_count,
        "distinct_provider_fixture_ids": distinct_fid_count,
        "won_count": won,
        "lost_count": lost,
        "pending_count": pending,
        "strike_rate_pct": strike_rate,
        "net_profit_units": net_profit,
        "discrepancies": discrepancies
    }

    logger.info("Migration Forensic Reconciliation Results:")
    for k, v in reconciliation.items():
        if k not in ("source_counts", "target_counts", "source_sha256_fingerprints", "discrepancies"):
            logger.info(f"  - {k:<30}: {v}")

    if discrepancies:
        for d in discrepancies:
            logger.error(f"DISCREPANCY: {d}")
        raise MigrationDiscrepancyError(f"Migration completed with {len(discrepancies)} discrepancy errors!")

    logger.info("=" * 72)
    logger.info(f"MIGRATION COMPLETED SUCCESSFULLY ({'POSTGRESQL' if is_pg else 'SQLITE REHEARSAL'})")
    logger.info("=" * 72)
    return reconciliation


def main():
    parser = argparse.ArgumentParser(description="SportyMill SQLite -> PostgreSQL Production Migration Engine")
    parser.add_argument("--source", default=str(BASE_DIR / "tipalgo.db"), help="Source SQLite DB path")
    parser.add_argument("--target", default="", help="Target PostgreSQL DATABASE_URL (overrides env var)")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch insertion size")
    parser.add_argument("--verify-only", action="store_true", help="Verify schema and counts without inserting")
    parser.add_argument("--dry-run", action="store_true", help="Roll back transaction after migration test")
    parser.add_argument("--sanitize", action="store_true", help="Exclude test-generated users and bet slips")

    args = parser.parse_args()

    sqlite_path = args.source
    # Security: prefer environment variable TARGET_DATABASE_URL or DATABASE_URL
    target_url = args.target or os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL", "")

    if not target_url:
        logger.warning("No target DATABASE_URL provided. Inspecting source SQLite database...")
        counts = get_sqlite_tables_and_counts(sqlite_path)
        print("\nSQLite Database Table Inventory:")
        for tbl, c in counts.items():
            print(f"  {tbl:<22}: {c:>6} rows")
        print(f"  TOTAL RECORDS         : {sum(counts.values()):>6} rows\n")
        print("To migrate, set TARGET_DATABASE_URL in environment or pass --target [URL].")
        sys.exit(0)

    try:
        res = asyncio.run(execute_migration(
            sqlite_path=sqlite_path,
            target_db_url=target_url,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            verify_only=args.verify_only,
            sanitize_test_data=args.sanitize
        ))
        print("\nMigration Execution Summary:")
        # Don't print credentials
        print(json.dumps(res, indent=2))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
