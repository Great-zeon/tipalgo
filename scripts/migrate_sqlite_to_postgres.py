#!/usr/bin/env python3
"""
SportyMill Production Migration Engine
Transfers all historical football data, user accounts, and prediction records
from local SQLite (tipalgo.db) to Managed PostgreSQL (asyncpg) with full
provenance verification, table-by-table reconciliation, and zero data loss.
"""

import os
import sys
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

# Ensure project root is in path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func, text, case
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Import SQLAlchemy models from main
from main import (
    Base, User, AuthorizedDevice, LeagueTable, HistoricalMatch,
    PredictionLog, HeadToHead, BetSlip, CalibrationState, FeatureStore,
    Config
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration_engine")


def get_sqlite_tables_and_counts(sqlite_path: str) -> Dict[str, int]:
    """Retrieve all table names and row counts from source SQLite database."""
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


async def migrate_data(sqlite_path: str, target_db_url: str) -> Dict[str, Any]:
    """Execute complete table-by-table migration from SQLite to PostgreSQL."""
    logger.info("=" * 70)
    logger.info("SPORTYMILL PRODUCTION SQLITE -> POSTGRESQL MIGRATION")
    logger.info("=" * 70)
    logger.info(f"Source SQLite: {sqlite_path}")

    # Normalize PostgreSQL URL for asyncpg
    norm_url = target_db_url.strip()
    if norm_url.startswith("postgres://"):
        norm_url = norm_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif norm_url.startswith("postgresql://") and not norm_url.startswith("postgresql+asyncpg://"):
        norm_url = norm_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Sanitize URL for logging (hide credentials)
    safe_url = norm_url.split("@")[-1] if "@" in norm_url else norm_url
    logger.info(f"Target Database Host: ...@{safe_url}")

    # 1. Connect to SQLite
    src_conn = sqlite3.connect(sqlite_path)
    src_conn.row_factory = sqlite3.Row
    src_cur = src_conn.cursor()

    source_counts = get_sqlite_tables_and_counts(sqlite_path)
    logger.info(f"Source Tables & Row Counts: {source_counts}")

    # 2. Connect to Target Database & Initialize Schema
    target_engine = create_async_engine(
        norm_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True
    )

    async with target_engine.begin() as conn:
        logger.info("Creating destination schema via SQLAlchemy Base.metadata...")
        await conn.run_sync(Base.metadata.create_all)

    TargetSession = async_sessionmaker(target_engine, expire_on_commit=False)

    migrated_counts: Dict[str, int] = {}
    migration_order = [
        "users",
        "authorized_devices",
        "league_tables",
        "historical_matches",
        "head_to_head",
        "calibration_state",
        "bet_slips",
        "feature_store",
        "prediction_logs"
    ]

    async with TargetSession() as session:
        # Step A: Users
        if "users" in source_counts:
            src_cur.execute("SELECT * FROM users ORDER BY id ASC")
            rows = src_cur.fetchall()
            for r in rows:
                existing = await session.execute(select(User).where(User.username == r["username"]))
                if not existing.scalar_one_or_none():
                    user_obj = User(
                        id=r["id"],
                        username=r["username"],
                        hashed_password=r["hashed_password"],
                        is_active=bool(r["is_active"]),
                        is_admin=bool(r["is_admin"]),
                        bankroll=float(r["bankroll"]),
                        created_at=datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) if r["created_at"] else datetime.now(timezone.utc),
                        is_pro=bool(r["is_pro"]),
                        pro_expires_at=datetime.fromisoformat(str(r["pro_expires_at"]).replace("Z", "+00:00")) if r.get("pro_expires_at") and str(r["pro_expires_at"]) != "None" else None,
                        two_factor_secret=r.get("two_factor_secret")
                    )
                    session.add(user_obj)
            await session.commit()
            res = await session.execute(select(func.count(User.id)))
            migrated_counts["users"] = res.scalar() or 0
            logger.info(f"Users migrated: {migrated_counts['users']} rows")

        # Step B: Authorized Devices
        if "authorized_devices" in source_counts:
            src_cur.execute("SELECT * FROM authorized_devices ORDER BY id ASC")
            rows = src_cur.fetchall()
            for r in rows:
                existing = await session.execute(select(AuthorizedDevice).where(AuthorizedDevice.device_id == r["device_id"]))
                if not existing.scalar_one_or_none():
                    dev_obj = AuthorizedDevice(
                        id=r["id"],
                        device_id=r["device_id"],
                        device_name=r["device_name"],
                        user_agent=r.get("user_agent") or "",
                        ip_address=r.get("ip_address"),
                        is_active=bool(r["is_active"]),
                        last_login=datetime.fromisoformat(str(r["last_login"]).replace("Z", "+00:00")) if r["last_login"] else datetime.now(timezone.utc),
                        created_at=datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) if r["created_at"] else datetime.now(timezone.utc)
                    )
                    session.add(dev_obj)
            await session.commit()
            res = await session.execute(select(func.count(AuthorizedDevice.id)))
            migrated_counts["authorized_devices"] = res.scalar() or 0
            logger.info(f"Authorized devices migrated: {migrated_counts['authorized_devices']} rows")

        # Step C: League Tables (Batch size: 1000)
        if "league_tables" in source_counts:
            src_cur.execute("SELECT * FROM league_tables ORDER BY id ASC")
            total_lt = source_counts["league_tables"]
            batch_size = 1000
            offset = 0
            while True:
                src_cur.execute(f"SELECT * FROM league_tables ORDER BY id ASC LIMIT {batch_size} OFFSET {offset}")
                batch = src_cur.fetchall()
                if not batch:
                    break
                for r in batch:
                    existing = await session.execute(select(LeagueTable).where(
                        LeagueTable.league_id == r["league_id"],
                        LeagueTable.team_name == r["team_name"]
                    ))
                    if not existing.scalar_one_or_none():
                        lt_obj = LeagueTable(
                            id=r["id"],
                            league_id=r["league_id"],
                            team_name=r["team_name"],
                            position=r["position"],
                            played=r["played"],
                            won=r["won"],
                            drawn=r["drawn"],
                            lost=r["lost"],
                            goals_for=r["goals_for"],
                            goals_against=r["goals_against"],
                            goal_difference=r["goal_difference"],
                            points=r["points"],
                            last_5_form=r["last_5_form"],
                            home_played=r.get("home_played") or 0,
                            home_won=r.get("home_won") or 0,
                            home_drawn=r.get("home_drawn") or 0,
                            home_lost=r.get("home_lost") or 0,
                            home_goals_for=r.get("home_goals_for") or 0,
                            home_goals_against=r.get("home_goals_against") or 0,
                            away_played=r.get("away_played") or 0,
                            away_won=r.get("away_won") or 0,
                            away_drawn=r.get("away_drawn") or 0,
                            away_lost=r.get("away_lost") or 0,
                            away_goals_for=r.get("away_goals_for") or 0,
                            away_goals_against=r.get("away_goals_against") or 0,
                            xg_for=float(r.get("xg_for") or 0.0),
                            xg_against=float(r.get("xg_against") or 0.0),
                            xg_diff=float(r.get("xg_diff") or 0.0),
                            possession_avg=float(r.get("possession_avg") or 0.0),
                            shots_on_target_avg=float(r.get("shots_on_target_avg") or 0.0),
                            shots_against_avg=float(r.get("shots_against_avg") or 0.0),
                            corners_avg=float(r.get("corners_avg") or 5.0),
                            cards_avg=float(r.get("cards_avg") or 2.0),
                            clean_sheets=r.get("clean_sheets") or 0,
                            failed_to_score=r.get("failed_to_score") or 0,
                            updated_at=datetime.fromisoformat(str(r["updated_at"]).replace("Z", "+00:00")) if r.get("updated_at") else datetime.now(timezone.utc)
                        )
                        session.add(lt_obj)
                await session.commit()
                offset += batch_size
                logger.info(f"League tables progress: {min(offset, total_lt)} / {total_lt}")
            res = await session.execute(select(func.count(LeagueTable.id)))
            migrated_counts["league_tables"] = res.scalar() or 0
            logger.info(f"League tables migrated: {migrated_counts['league_tables']} rows")

        # Step D: Historical Matches (Batch size: 1000)
        if "historical_matches" in source_counts:
            total_hm = source_counts["historical_matches"]
            batch_size = 1000
            offset = 0
            while True:
                src_cur.execute(f"SELECT * FROM historical_matches ORDER BY id ASC LIMIT {batch_size} OFFSET {offset}")
                batch = src_cur.fetchall()
                if not batch:
                    break
                for r in batch:
                    ext_id = r["external_id"]
                    existing = await session.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == ext_id))
                    if not existing.scalar_one_or_none():
                        m_date_raw = str(r["match_date"]) if r["match_date"] else None
                        try:
                            m_date = datetime.fromisoformat(m_date_raw.replace("Z", "+00:00")) if m_date_raw else datetime.now(timezone.utc)
                        except Exception:
                            try:
                                m_date = datetime.strptime(m_date_raw[:19], "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                m_date = datetime.now(timezone.utc)

                        hm_obj = HistoricalMatch(
                            id=r["id"],
                            external_id=ext_id,
                            home_team=r["home_team"],
                            away_team=r["away_team"],
                            home_logo=r.get("home_logo"),
                            away_logo=r.get("away_logo"),
                            home_score=r["home_score"],
                            away_score=r["away_score"],
                            league_id=r["league_id"],
                            match_date=m_date,
                            home_odds=r["home_odds"],
                            draw_odds=r["draw_odds"],
                            away_odds=r["away_odds"],
                            status=r["status"],
                            source_classification=r.get("source_classification") or "unverified",
                            provider=r.get("provider") or "manual",
                            provider_fixture_id=r.get("provider_fixture_id"),
                            provider_competition_id=r.get("provider_competition_id"),
                            home_team_id=r.get("home_team_id"),
                            away_team_id=r.get("away_team_id"),
                            provider_status=r.get("provider_status"),
                            raw_payload=r.get("raw_payload")
                        )
                        session.add(hm_obj)
                await session.commit()
                offset += batch_size
                logger.info(f"Historical matches progress: {min(offset, total_hm)} / {total_hm}")
            res = await session.execute(select(func.count(HistoricalMatch.id)))
            migrated_counts["historical_matches"] = res.scalar() or 0
            logger.info(f"Historical matches migrated: {migrated_counts['historical_matches']} rows")

        # Step E: Head to Head
        if "head_to_head" in source_counts:
            src_cur.execute("SELECT * FROM head_to_head ORDER BY id ASC")
            rows = src_cur.fetchall()
            for r in rows:
                existing = await session.execute(select(HeadToHead).where(HeadToHead.id == r["id"]))
                if not existing.scalar_one_or_none():
                    h2h_obj = HeadToHead(
                        id=r["id"],
                        home_team=r["home_team"],
                        away_team=r["away_team"],
                        match_date=datetime.fromisoformat(str(r["match_date"]).replace("Z", "+00:00")) if r["match_date"] else datetime.now(timezone.utc),
                        home_score=r["home_score"],
                        away_score=r["away_score"],
                        league=r["league"],
                        home_scored=bool(r["home_scored"]),
                        away_scored=bool(r["away_scored"]),
                        venue=r.get("venue")
                    )
                    session.add(h2h_obj)
            await session.commit()
            res = await session.execute(select(func.count(HeadToHead.id)))
            migrated_counts["head_to_head"] = res.scalar() or 0
            logger.info(f"Head to head records migrated: {migrated_counts['head_to_head']} rows")

        # Step F: Bet Slips
        if "bet_slips" in source_counts:
            src_cur.execute("SELECT * FROM bet_slips ORDER BY id ASC")
            rows = src_cur.fetchall()
            for r in rows:
                existing = await session.execute(select(BetSlip).where(BetSlip.slip_code == r["slip_code"]))
                if not existing.scalar_one_or_none():
                    bs_obj = BetSlip(
                        id=r["id"],
                        user_id=r["user_id"],
                        slip_code=r["slip_code"],
                        total_odds=float(r["total_odds"]),
                        stake=float(r["stake"]),
                        potential_payout=float(r["potential_payout"]),
                        items=r["items"],
                        created_at=datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) if r["created_at"] else datetime.now(timezone.utc),
                        status=r["status"]
                    )
                    session.add(bs_obj)
            await session.commit()
            res = await session.execute(select(func.count(BetSlip.id)))
            migrated_counts["bet_slips"] = res.scalar() or 0
            logger.info(f"Bet slips migrated: {migrated_counts['bet_slips']} rows")

        # Step G: Prediction Logs (Preserving all 366 provider-verified records & quarantined records)
        if "prediction_logs" in source_counts:
            src_cur.execute("SELECT * FROM prediction_logs ORDER BY id ASC")
            rows = src_cur.fetchall()
            for r in rows:
                p_id = r["prediction_id"]
                existing = await session.execute(select(PredictionLog).where(PredictionLog.prediction_id == p_id))
                if not existing.scalar_one_or_none():
                    pl_obj = PredictionLog(
                        id=r["id"],
                        prediction_id=p_id,
                        endpoint=r["endpoint"],
                        home_team=r["home_team"],
                        away_team=r["away_team"],
                        match_date=datetime.fromisoformat(str(r["match_date"]).replace("Z", "+00:00")) if r["match_date"] else datetime.now(timezone.utc),
                        fixture_json=r["fixture_json"],
                        raw_probs_json=r["raw_probs_json"],
                        market_probs_json=r.get("market_probs_json"),
                        processed=bool(r["processed"]),
                        created_at=datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) if r["created_at"] else datetime.now(timezone.utc),
                        sub_strategies_json=r.get("sub_strategies_json"),
                        confidence_percent=float(r["confidence_percent"]) if r.get("confidence_percent") is not None else None,
                        criteria_breakdown_json=r.get("criteria_breakdown_json"),
                        playbook_adjustments_applied=bool(r.get("playbook_adjustments_applied", False)),
                        outcome=r.get("outcome"),
                        actual_home_score=r.get("actual_home_score"),
                        actual_away_score=r.get("actual_away_score"),
                        unit_return=float(r["unit_return"]) if r.get("unit_return") is not None else None,
                        settled_at=datetime.fromisoformat(str(r["settled_at"]).replace("Z", "+00:00")) if r.get("settled_at") else None,
                        feedback_notes=r.get("feedback_notes"),
                        source_classification=r.get("source_classification") or "unverified",
                        provider_fixture_id=r.get("provider_fixture_id"),
                        model_version=r.get("model_version") or "v21.0"
                    )
                    session.add(pl_obj)
            await session.commit()
            res = await session.execute(select(func.count(PredictionLog.id)))
            migrated_counts["prediction_logs"] = res.scalar() or 0
            logger.info(f"Prediction logs migrated: {migrated_counts['prediction_logs']} rows")

    src_conn.close()

    # Step H: Post-Migration Forensic Reconciliation
    logger.info("=" * 70)
    logger.info("POST-MIGRATION FORENSIC RECONCILIATION")
    logger.info("=" * 70)

    async with TargetSession() as session:
        # Check provider-verified prediction count
        pv_res = await session.execute(
            select(func.count(PredictionLog.id)).where(PredictionLog.source_classification == "provider_verified")
        )
        pv_count = pv_res.scalar() or 0

        # Check unique provider fixture IDs
        fid_res = await session.execute(
            select(func.count(func.distinct(PredictionLog.provider_fixture_id)))
            .where(
                PredictionLog.source_classification == "provider_verified",
                PredictionLog.provider_fixture_id.isnot(None)
            )
        )
        distinct_fid_count = fid_res.scalar() or 0

        # Check financial performance & win/loss
        outcomes_res = await session.execute(
            select(
                func.count(PredictionLog.id).label("total"),
                func.sum(case((PredictionLog.outcome == "correct", 1), else_=0)).label("won"),
                func.sum(case((PredictionLog.outcome == "incorrect", 1), else_=0)).label("lost"),
                func.sum(PredictionLog.unit_return).label("net_profit")
            ).where(PredictionLog.source_classification == "provider_verified")
        )
        o_row = outcomes_res.fetchone()
        won = int(o_row.won or 0)
        lost = int(o_row.lost or 0)
        net_profit = round(float(o_row.net_profit or 0.0), 2)
        strike_rate = round((won / (won + lost) * 100), 1) if (won + lost) > 0 else 0.0

    reconciliation = {
        "source_sqlite_path": sqlite_path,
        "source_table_counts": source_counts,
        "migrated_table_counts": migrated_counts,
        "table_reconciliation_match": all(
            migrated_counts.get(t, 0) == source_counts.get(t, 0) for t in source_counts
        ),
        "provider_verified_predictions": pv_count,
        "expected_provider_verified": 366,
        "distinct_provider_fixture_ids": distinct_fid_count,
        "won_count": won,
        "lost_count": lost,
        "strike_rate_pct": strike_rate,
        "net_profit_units": net_profit
    }

    logger.info(f"Reconciliation Result: {json.dumps(reconciliation, indent=2)}")
    return reconciliation


if __name__ == "__main__":
    sqlite_file = sys.argv[1] if len(sys.argv) > 1 else str(BASE_DIR / "tipalgo.db")
    target_url = sys.argv[2] if len(sys.argv) > 2 else os.getenv("DATABASE_URL", "")

    if not target_url or "sqlite" in target_url:
        logger.warning("No PostgreSQL DATABASE_URL supplied. Validating SQLite backup and schema only.")
        counts = get_sqlite_tables_and_counts(sqlite_file)
        print(f"SQLite Tables and Counts for {sqlite_file}:")
        for tbl, c in counts.items():
            print(f"  - {tbl}: {c} rows")
        sys.exit(0)

    result = asyncio.run(migrate_data(sqlite_file, target_url))
    if not result["table_reconciliation_match"]:
        logger.error("MIGRATION DISCREPANCY DETECTED: Row counts did not match perfectly!")
        sys.exit(1)
    else:
        logger.info("MIGRATION SUCCESSFUL: All tables and authentic prediction records preserved perfectly!")
        sys.exit(0)
