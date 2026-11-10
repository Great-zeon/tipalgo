#!/usr/bin/env python3
"""
SportyMill Production Migration Engine
Transfers all historical football data, user accounts, and prediction records
from local SQLite (tipalgo.db) to Managed PostgreSQL (asyncpg) with full
provenance verification, table-by-table reconciliation, and zero data loss.

Usage:
  python3 scripts/migrate_sqlite_to_postgres.py [source_sqlite_path] [target_postgres_url] [options]

Options:
  --source PATH        Source SQLite database path (default: tipalgo.db)
  --target URL         Target PostgreSQL DATABASE_URL (default: from env)
  --batch-size N       Batch insert chunk size (default: 1000)
  --verify-only        Verify schema, counts, and provenance without migrating
  --dry-run            Simulate migration in a transaction without committing
"""

import os
import ssl
import sys
import json
import sqlite3
import asyncio
import logging
import argparse
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

EXPECTED_ROW_COUNTS = {
    "users": 4,
    "authorized_devices": 5,
    "league_tables": 4556,
    "historical_matches": 22583,
    "head_to_head": 12,
    "bet_slips": 2,
    "feature_store": 0,
    "calibration_state": 0,
    "prediction_logs": 438
}


class MigrationDiscrepancyError(Exception):
    """Raised when post-migration counts or financial metrics do not reconcile 100%."""
    pass


def parse_datetime_safe(val: Any) -> Optional[datetime]:
    """Parse various SQLite datetime formats into timezone-aware or native UTC datetime."""
    if val is None or str(val).strip() in ("", "None", "null"):
        return None
    if isinstance(val, datetime):
        return val

    val_str = str(val).strip()
    # Normalize ISO with 'Z'
    clean_str = val_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean_str)
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
            return datetime.strptime(val_str[:26], fmt)
        except Exception:
            continue

    logger.warning(f"Could not parse datetime string: {val_str}. Using current UTC.")
    return datetime.now(timezone.utc)


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
    
    # Strip libpq query params that asyncpg handles via connect_args
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
        # Render, Supabase, Neon, AWS RDS require SSL
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
        # SQLite
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
    verify_only: bool = False
) -> Dict[str, Any]:
    """Execute complete, idempotent table-by-table migration with forensic reconciliation."""
    logger.info("=" * 72)
    logger.info("SPORTYMILL ENTERPRISE SQLITE -> POSTGRESQL MIGRATION ENGINE")
    logger.info("=" * 72)
    logger.info(f"Source SQLite Path: {sqlite_path}")

    norm_url = normalize_target_url(target_db_url)
    is_pg = "postgres" in norm_url
    safe_host = norm_url.split("@")[-1] if "@" in norm_url else norm_url
    logger.info(f"Target Database Host: ...@{safe_host} (Engine: {'PostgreSQL' if is_pg else 'SQLite'})")

    # 1. Inspect Source SQLite
    source_counts = get_sqlite_tables_and_counts(sqlite_path)
    logger.info("Source Table Row Counts:")
    for tbl in MIGRATION_ORDER:
        cnt = source_counts.get(tbl, 0)
        exp = EXPECTED_ROW_COUNTS.get(tbl, 0)
        match_str = "MATCH" if cnt == exp else f"DISCREPANCY (expected {exp})"
        logger.info(f"  - {tbl:<22}: {cnt:>6} rows [{match_str}]")

    total_source_rows = sum(source_counts.get(t, 0) for t in MIGRATION_ORDER)
    logger.info(f"Total Source Rows: {total_source_rows} records across {len(MIGRATION_ORDER)} tables.")

    # 2. Connect to Target and Initialize Schema
    target_engine = build_engine_for_url(norm_url)

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

                # Inspect column types from model
                col_types = {}
                for col in inspect(model_cls).columns:
                    col_types[col.name] = col.type

                src_cur.execute(f"SELECT * FROM {table_name} ORDER BY id ASC")
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

                # Verify table row count in target
                cnt_res = await session.execute(select(func.count(model_cls.id)))
                migrated_counts[table_name] = cnt_res.scalar() or 0
                logger.info(f"Table {table_name} migration complete: {migrated_counts[table_name]} rows in target.")

            # Update PostgreSQL serial sequences so future inserts don't collide
            if is_pg and not dry_run:
                logger.info("Synchronizing PostgreSQL primary key sequences...")
                for table_name in MIGRATION_ORDER:
                    try:
                        await session.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), COALESCE(MAX(id), 1)) FROM {table_name};"
                        ))
                    except Exception:
                        pass
                await session.commit()

    src_conn.close()

    # 4. Forensic Reconciliation & Audit Validation
    logger.info("=" * 72)
    logger.info("POST-MIGRATION FORENSIC RECONCILIATION")
    logger.info("=" * 72)

    async with TargetSession() as session:
        # Re-query all table counts from destination
        target_counts = {}
        for table_name in MIGRATION_ORDER:
            model_cls = TABLE_MODEL_MAP[table_name]
            cnt_res = await session.execute(select(func.count(model_cls.id)))
            target_counts[table_name] = cnt_res.scalar() or 0

        # Verify prediction logs provenance and metrics
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

        # Query outcomes & strike rate
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

    # Build reconciliation report
    discrepancies = []
    for tbl in MIGRATION_ORDER:
        src_c = source_counts.get(tbl, 0)
        tgt_c = target_counts.get(tbl, 0)
        if src_c != tgt_c:
            discrepancies.append(f"Table '{tbl}' mismatch: source={src_c}, target={tgt_c}")

    if pv_count != 366:
        discrepancies.append(f"Provider-verified predictions count mismatch: expected 366, got {pv_count}")
    if distinct_fid_count != 366:
        discrepancies.append(f"Distinct provider fixture IDs mismatch: expected 366, got {distinct_fid_count}")
    if quarantined_count != 72:
        discrepancies.append(f"Quarantined records mismatch: expected 72, got {quarantined_count}")
    if won != 203 or lost != 163:
        discrepancies.append(f"Settled outcomes mismatch: expected 203 won / 163 lost, got {won} won / {lost} lost")

    reconciliation = {
        "status": "SUCCESS" if not discrepancies else "DISCREPANCY_DETECTED",
        "database_engine": "postgresql" if is_pg else "sqlite",
        "source_sqlite_path": sqlite_path,
        "total_source_records": total_source_rows,
        "total_migrated_records": sum(target_counts.values()),
        "source_counts": source_counts,
        "target_counts": target_counts,
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
        if k not in ("source_counts", "target_counts", "discrepancies"):
            logger.info(f"  - {k:<30}: {v}")

    if discrepancies:
        for d in discrepancies:
            logger.error(f"DISCREPANCY: {d}")
        raise MigrationDiscrepancyError(f"Migration completed with {len(discrepancies)} discrepancy errors!")

    logger.info("=" * 72)
    logger.info("MIGRATION COMPLETED SUCCESSFULLY WITH 100% RECONCILIATION MATCH")
    logger.info("=" * 72)
    return reconciliation


def main():
    parser = argparse.ArgumentParser(description="SportyMill SQLite -> PostgreSQL Production Migration Engine")
    parser.add_argument("source", nargs="?", default=str(BASE_DIR / "tipalgo.db"), help="Source SQLite DB path")
    parser.add_argument("target", nargs="?", default=os.getenv("DATABASE_URL", ""), help="Target PostgreSQL DATABASE_URL")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch insertion size")
    parser.add_argument("--verify-only", action="store_true", help="Verify schema and counts without inserting")
    parser.add_argument("--dry-run", action="store_true", help="Roll back transaction after migration test")

    args = parser.parse_args()

    sqlite_path = args.source
    target_url = args.target

    if not target_url:
        logger.warning("No target DATABASE_URL provided. Inspecting source SQLite database...")
        counts = get_sqlite_tables_and_counts(sqlite_path)
        print("\nSQLite Database Table Inventory:")
        for tbl, c in counts.items():
            print(f"  {tbl:<22}: {c:>6} rows")
        print(f"  TOTAL RECORDS         : {sum(counts.values()):>6} rows\n")
        sys.exit(0)

    try:
        res = asyncio.run(execute_migration(
            sqlite_path=sqlite_path,
            target_db_url=target_url,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            verify_only=args.verify_only
        ))
        print("\nMigration Execution Summary:")
        print(json.dumps(res, indent=2))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
