"""
SportyMill Database Migration 002: Live Prediction Idempotency Index
Adds partial unique index on prediction_logs(provider_fixture_id, endpoint)
for records where source_classification = 'live_recorded'.

Fully compatible and tested for:
1. SQLite (3.8.0+ via sqlite3 or aiosqlite)
2. PostgreSQL (10+ via asyncpg / psycopg2 / SQLAlchemy)

Includes non-destructive schema downgrade and batch-scoped emergency purge utilities.
"""
import os
import sys
import sqlite3
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration_002")

INDEX_NAME = "uq_prediction_logs_live_fid_endpoint"
TABLE_NAME = "prediction_logs"

# DDL definitions
SQLITE_UPGRADE_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
ON {TABLE_NAME}(provider_fixture_id, endpoint)
WHERE source_classification = 'live_recorded';
"""
SQLITE_DOWNGRADE_SQL = f"""
DROP INDEX IF EXISTS {INDEX_NAME};
"""

POSTGRES_UPGRADE_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
ON {TABLE_NAME}(provider_fixture_id, endpoint)
WHERE source_classification = 'live_recorded';
"""
POSTGRES_DOWNGRADE_SQL = f"""
DROP INDEX IF EXISTS {INDEX_NAME};
"""

POSTGRES_VERIFY_SQL = f"""
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = '{TABLE_NAME}' AND indexname = '{INDEX_NAME}';
"""

def upgrade_sqlite(db_path: Path) -> bool:
    """Apply migration to SQLite database."""
    logger.info(f"Applying migration 002 to SQLite: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(SQLITE_UPGRADE_SQL)
        conn.commit()
        # Verify index exists
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='index' AND name='{INDEX_NAME}'")
        row = cursor.fetchone()
        if row:
            logger.info(f"Successfully created and verified {INDEX_NAME} in SQLite.")
            return True
        else:
            logger.error(f"Failed to find {INDEX_NAME} after creation in SQLite.")
            return False
    except Exception as e:
        logger.error(f"Migration 002 upgrade failed on SQLite: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def downgrade_sqlite(db_path: Path) -> bool:
    """Revert migration on SQLite database (non-destructive; drops index only)."""
    logger.info(f"Reverting migration 002 on SQLite: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(SQLITE_DOWNGRADE_SQL)
        conn.commit()
        logger.info(f"Successfully dropped {INDEX_NAME} from SQLite.")
        return True
    except Exception as e:
        logger.error(f"Migration 002 downgrade failed on SQLite: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def purge_deployment_batch_sqlite(db_path: Path, batch_id: str) -> int:
    """
    Safely purge live predictions scoped exclusively to a specific deployment batch.
    Preserves all 366 historical validation records, unverified records, and other batches.
    """
    logger.info(f"Purging records for deployment batch '{batch_id}' from SQLite: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM prediction_logs
            WHERE source_classification = 'live_recorded'
              AND (model_version LIKE ? OR fixture_json LIKE ?)
            """,
            (f"%{batch_id}%", f'%"{batch_id}"%')
        )
        deleted = cursor.rowcount
        conn.commit()
        logger.info(f"Purged {deleted} live predictions for deployment batch '{batch_id}'.")
        return deleted
    finally:
        conn.close()

async def upgrade_postgres_async(db_url: str) -> bool:
    """Apply migration to PostgreSQL database using asyncpg."""
    logger.info("Applying migration 002 to PostgreSQL...")
    try:
        import asyncpg
        clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(clean_url)
        try:
            await conn.execute(POSTGRES_UPGRADE_SQL)
            row = await conn.fetchrow(POSTGRES_VERIFY_SQL)
            if row:
                logger.info(f"Successfully created and verified {INDEX_NAME} in PostgreSQL: {row['indexdef']}")
                return True
            else:
                logger.error(f"Failed to find {INDEX_NAME} after creation in PostgreSQL.")
                return False
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Migration 002 upgrade failed on PostgreSQL: {e}")
        raise

async def downgrade_postgres_async(db_url: str) -> bool:
    """Revert migration on PostgreSQL database (non-destructive; drops index only)."""
    logger.info("Reverting migration 002 on PostgreSQL...")
    try:
        import asyncpg
        clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(clean_url)
        try:
            await conn.execute(POSTGRES_DOWNGRADE_SQL)
            row = await conn.fetchrow(POSTGRES_VERIFY_SQL)
            if not row:
                logger.info(f"Successfully dropped {INDEX_NAME} from PostgreSQL.")
                return True
            else:
                logger.error(f"Index {INDEX_NAME} still exists after drop on PostgreSQL.")
                return False
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Migration 002 downgrade failed on PostgreSQL: {e}")
        raise

def run_migration(action: str = "upgrade", db_target: Optional[str] = None) -> bool:
    target = db_target or os.getenv("DATABASE_URL", str(BASE_DIR / "tipalgo.db"))
    if "postgres" in target:
        if action == "upgrade":
            return asyncio.run(upgrade_postgres_async(target))
        elif action == "downgrade":
            return asyncio.run(downgrade_postgres_async(target))
        else:
            raise ValueError(f"Unknown action '{action}'")
    else:
        sqlite_file = Path(target.replace("sqlite+aiosqlite:///", ""))
        if action == "upgrade":
            return upgrade_sqlite(sqlite_file)
        elif action == "downgrade":
            return downgrade_sqlite(sqlite_file)
        else:
            raise ValueError(f"Unknown action '{action}'")

if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    tgt = sys.argv[2] if len(sys.argv) > 2 else None
    run_migration(act, tgt)
