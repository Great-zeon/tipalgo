#!/usr/bin/env python3
"""
SportyMill Production PostgreSQL Migration Preflight Tool
Safe, strictly read-only inspection utility to validate SQLite source integrity,
PostgreSQL destination connectivity, database version, SSL configuration, permissions,
connection limits, and destination table states prior to executing data migration.

Usage:
  python3 scripts/preflight_production_postgres_migration.py [options]

Options:
  --source PATH        Source SQLite database path (default: tipalgo.db)
  --target URL         Target PostgreSQL DATABASE_URL (default: from env)
  --output JSON_PATH   Path to write machine-readable preflight report
  --strict             Fail if destination contains existing rows
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
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("preflight_inspector")

EXPECTED_TABLES = [
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

def redact_db_url(url: str) -> str:
    """Safely redact sensitive credentials from database URLs for logging and reports."""
    if not url:
        return "[NOT SET]"
    try:
        # Match pattern: scheme://user:pass@host:port/db
        pattern = r"(^[a-zA-Z0-9_\+]+://)([^:]+):([^@]+)@(.*$)"
        match = re.match(pattern, url.strip())
        if match:
            scheme, user, _pwd, rest = match.groups()
            return f"{scheme}{user}:[REDACTED]@{rest}"
        # If no password in user@host
        pattern_nopass = r"(^[a-zA-Z0-9_\+]+://)([^@]+)@(.*$)"
        match_nopass = re.match(pattern_nopass, url.strip())
        if match_nopass:
            scheme, user, rest = match_nopass.groups()
            return f"{scheme}{user}@{rest}"
        if "sqlite" in url:
            return url.strip()
        return "[REDACTED_URL]"
    except Exception:
        return "[REDACTED_URL]"


def normalize_db_url(raw_url: str) -> str:
    """Normalize target connection URL for asyncpg."""
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


def inspect_sqlite_source(sqlite_path: str) -> Dict[str, Any]:
    """Execute read-only validation of source SQLite database."""
    res = {
        "status": "PASS",
        "file_path": sqlite_path,
        "exists": False,
        "file_size_bytes": 0,
        "integrity_check": "FAILED",
        "tables": {},
        "total_source_rows": 0,
        "errors": []
    }

    if not os.path.exists(sqlite_path):
        res["status"] = "FAIL"
        res["errors"].append(f"Source SQLite file does not exist at '{sqlite_path}'")
        return res

    res["exists"] = True
    res["file_size_bytes"] = os.path.getsize(sqlite_path)

    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        cursor = conn.cursor()

        # Integrity Check
        cursor.execute("PRAGMA integrity_check;")
        check_val = cursor.fetchone()
        if check_val and check_val[0] == "ok":
            res["integrity_check"] = "OK"
        else:
            res["integrity_check"] = str(check_val)
            res["status"] = "FAIL"
            res["errors"].append(f"SQLite PRAGMA integrity_check failed: {check_val}")

        # Table counts
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables_found = [row[0] for row in cursor.fetchall()]

        total_rows = 0
        for tbl in EXPECTED_TABLES:
            if tbl in tables_found:
                cursor.execute(f"SELECT count(*) FROM {tbl};")
                cnt = cursor.fetchone()[0]
                res["tables"][tbl] = {"exists": True, "row_count": cnt}
                total_rows += cnt
            else:
                res["tables"][tbl] = {"exists": False, "row_count": 0}
                res["status"] = "FAIL"
                res["errors"].append(f"Expected application table '{tbl}' missing from source SQLite!")

        res["total_source_rows"] = total_rows

        # Specific integrity checks
        cursor.execute("SELECT count(*) FROM prediction_logs WHERE source_classification = 'provider_verified';")
        pv_count = cursor.fetchone()[0]
        res["provider_verified_count"] = pv_count

        cursor.execute("SELECT count(*) FROM prediction_logs WHERE source_classification = 'live_recorded';")
        lr_count = cursor.fetchone()[0]
        res["live_recorded_count"] = lr_count

        cursor.execute("SELECT count(DISTINCT provider_fixture_id) FROM prediction_logs WHERE source_classification = 'provider_verified' AND provider_fixture_id IS NOT NULL;")
        distinct_fid = cursor.fetchone()[0]
        res["distinct_provider_fixture_ids"] = distinct_fid

        conn.close()
    except Exception as e:
        res["status"] = "FAIL"
        res["errors"].append(f"SQLite inspection error: {str(e)}")

    return res


async def inspect_postgres_target(target_url: str, strict: bool = False) -> Dict[str, Any]:
    """Execute read-only validation of destination database without writing or creating tables."""
    norm_url = normalize_db_url(target_url)
    is_pg = "postgres" in norm_url

    res = {
        "status": "PASS",
        "redacted_target_url": redact_db_url(norm_url),
        "engine_type": "postgresql" if is_pg else "sqlite",
        "connection": "NOT TESTED",
        "ssl_configured": False,
        "database_version": "UNKNOWN",
        "current_user": "UNKNOWN",
        "current_database": "UNKNOWN",
        "max_connections": "UNKNOWN",
        "existing_tables": {},
        "existing_rows_detected": 0,
        "errors": [],
        "warnings": []
    }

    if not is_pg and "sqlite" not in norm_url:
        res["status"] = "FAIL"
        res["errors"].append("Target URL is neither a valid PostgreSQL (asyncpg) nor SQLite staging URL.")
        return res

    connect_args = {}
    if is_pg:
        # Require SSL for cloud managed databases
        if any(h in norm_url for h in ("render.com", "neon.tech", "supabase.co", "rds.amazonaws.com")) or "ssl" in norm_url:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ssl_ctx
            res["ssl_configured"] = True

    try:
        engine = create_async_engine(
            norm_url,
            echo=False,
            pool_size=2,
            max_overflow=0,
            connect_args=connect_args
        )

        async with engine.connect() as conn:
            res["connection"] = "SUCCESSFUL"

            if is_pg:
                # Query version and user metadata safely (READ-ONLY)
                v_res = await conn.execute(text("SELECT version();"))
                res["database_version"] = v_res.scalar()

                u_res = await conn.execute(text("SELECT current_user, current_database(), current_schema();"))
                row = u_res.fetchone()
                if row:
                    res["current_user"] = row[0]
                    res["current_database"] = row[1]
                    res["current_schema"] = row[2]

                try:
                    c_res = await conn.execute(text("SHOW max_connections;"))
                    res["max_connections"] = c_res.scalar()
                except Exception:
                    pass

                # Inspect existing tables in destination schema
                t_stmt = text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE';"
                )
                t_res = await conn.execute(t_stmt)
                dest_tables = [r[0] for r in t_res.fetchall()]

                existing_rows = 0
                for tbl in EXPECTED_TABLES:
                    if tbl in dest_tables:
                        try:
                            cnt_stmt = text(f"SELECT count(*) FROM {tbl};")
                            cnt_res = await conn.execute(cnt_stmt)
                            cnt = cnt_res.scalar() or 0
                            res["existing_tables"][tbl] = {"exists": True, "row_count": cnt}
                            existing_rows += cnt
                        except Exception as ce:
                            res["existing_tables"][tbl] = {"exists": True, "error": str(ce)}
                    else:
                        res["existing_tables"][tbl] = {"exists": False, "row_count": 0}

                res["existing_rows_detected"] = existing_rows

                if existing_rows > 0:
                    msg = (
                        f"Destination database already contains {existing_rows} records across "
                        f"{len([t for t, d in res['existing_tables'].items() if d.get('row_count', 0) > 0])} tables."
                    )
                    if strict:
                        res["status"] = "FAIL"
                        res["errors"].append(msg + " Strict mode forbids migration into non-empty database.")
                    else:
                        res["warnings"].append(msg + " Migration will execute idempotent upserts (ON CONFLICT DO UPDATE/NOTHING).")
            else:
                # SQLite Staging Target Inspection
                res["database_version"] = "SQLite Staging Engine"
                res["ssl_configured"] = False
                res["warnings"].append("Target is local SQLite staging rather than remote Managed PostgreSQL.")

        await engine.dispose()
    except Exception as e:
        res["status"] = "FAIL"
        res["connection"] = "FAILED"
        res["errors"].append(f"Target connection failed: {str(e)}")

    return res


def generate_preflight_verdict(src_res: Dict[str, Any], tgt_res: Dict[str, Any]) -> Dict[str, Any]:
    """Generate final authoritative preflight verdict and pre-flight checklist."""
    errors = src_res.get("errors", []) + tgt_res.get("errors", [])
    warnings = tgt_res.get("warnings", [])

    if errors:
        verdict = "NOT_READY"
        summary = f"Preflight checks failed with {len(errors)} blocking errors."
    elif warnings:
        verdict = "READY_WITH_NOTICES"
        summary = f"Preflight checks passed with {len(warnings)} operational notices."
    else:
        verdict = "READY_FOR_MIGRATION"
        summary = "All preflight checks passed. Destination is clean, accessible, and ready for migration."

    return {
        "verdict": verdict,
        "summary": summary,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": src_res,
        "target": tgt_res,
        "errors": errors,
        "warnings": warnings
    }


def print_human_report(report: Dict[str, Any]):
    """Print clean, executive-level human-readable preflight summary."""
    src = report["source"]
    tgt = report["target"]

    print("=" * 80)
    print("SPORTYMILL PRODUCTION POSTGRESQL MIGRATION PREFLIGHT REPORT")
    print("=" * 80)
    print(f"Timestamp (UTC)     : {report['timestamp_utc']}")
    print(f"Preflight Verdict   : {report['verdict']}")
    print(f"Executive Summary   : {report['summary']}")
    print("-" * 80)
    print("SOURCE SQLITE INSPECTION (READ-ONLY):")
    print(f"  File Path         : {src.get('file_path')}")
    print(f"  File Status       : {'EXISTS' if src.get('exists') else 'MISSING'} ({src.get('file_size_bytes', 0):,} bytes)")
    print(f"  PRAGMA Integrity  : {src.get('integrity_check')}")
    print(f"  Total Source Rows : {src.get('total_source_rows', 0):,} records across {len(src.get('tables', {}))} tables")
    print(f"  Provider Verified : {src.get('provider_verified_count', 0)} backtest fixtures (Distinct IDs: {src.get('distinct_provider_fixture_ids', 0)})")
    print(f"  Live Recorded     : {src.get('live_recorded_count', 0)} pre-match recommendations")
    print("-" * 80)
    print("DESTINATION INSPECTION (READ-ONLY):")
    print(f"  Target URI        : {tgt.get('redacted_target_url')}")
    print(f"  Engine Type       : {tgt.get('engine_type').upper()}")
    print(f"  Connection Status : {tgt.get('connection')}")
    print(f"  SSL Configuration : {'ENFORCED' if tgt.get('ssl_configured') else 'NONE'}")
    print(f"  Server Version    : {tgt.get('database_version')}")
    print(f"  Database / User   : {tgt.get('current_database')} / {tgt.get('current_user')}")
    print(f"  Max Connections   : {tgt.get('max_connections')}")
    print(f"  Existing Records  : {tgt.get('existing_rows_detected', 0):,} rows detected in destination")
    print("-" * 80)
    print("PREFLIGHT TABLE CENSUS:")
    print(f"{'Table Name':<22} | {'Source Rows':<12} | {'Target Status':<20} | {'Target Rows':<12}")
    print("-" * 80)
    for tbl in EXPECTED_TABLES:
        s_cnt = src.get("tables", {}).get(tbl, {}).get("row_count", 0)
        t_info = tgt.get("existing_tables", {}).get(tbl, {})
        t_exists = t_info.get("exists", False)
        t_cnt = t_info.get("row_count", 0) if t_exists else "-"
        t_stat = "EXISTS" if t_exists else "EMPTY / NOT CREATED"
        print(f"{tbl:<22} | {s_cnt:<12} | {t_stat:<20} | {str(t_cnt):<12}")
    print("=" * 80)

    if report["warnings"]:
        print("OPERATIONAL NOTICES / WARNINGS:")
        for w in report["warnings"]:
            print(f"  [!] {w}")
        print("-" * 80)

    if report["errors"]:
        print("BLOCKING ERRORS:")
        for e in report["errors"]:
            print(f"  [X] {e}")
        print("-" * 80)


def main():
    parser = argparse.ArgumentParser(description="SportyMill Read-Only Production PostgreSQL Preflight Inspector")
    parser.add_argument("--source", default=str(BASE_DIR / "tipalgo.db"), help="Source SQLite database path")
    parser.add_argument("--target", default=os.getenv("DATABASE_URL", ""), help="Target PostgreSQL connection URI")
    parser.add_argument("--output", default=None, help="Optional path to write JSON preflight report")
    parser.add_argument("--strict", action="store_true", help="Reject target if any records exist")

    args = parser.parse_args()

    src_res = inspect_sqlite_source(args.source)

    target_url = args.target
    if not target_url:
        logger.warning("No TARGET DATABASE_URL supplied in arguments or environment. Using SQLite self-check.")
        target_url = f"sqlite+aiosqlite:///{args.source}"

    tgt_res = asyncio.run(inspect_postgres_target(target_url, strict=args.strict))

    report = generate_preflight_verdict(src_res, tgt_res)
    print_human_report(report)

    if args.output:
        try:
            with open(args.output, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"Machine-readable preflight report written to: {args.output}")
        except Exception as e:
            logger.error(f"Failed to write output JSON: {e}")

    if report["verdict"] == "NOT_READY":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

