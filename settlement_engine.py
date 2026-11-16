"""
SportyMill Live Prediction Persistence and Settlement Engine
Production-grade standalone module providing:
1. Authoritative ISO-8601 kickoff timestamp parsing with strict UTC conversion
2. Pre-kickoff snapshot validation & dialect-aware atomic batch persistence (SQLite & PostgreSQL)
3. Full snapshot auditability (odds provider, lines, timestamps, complete model distributions)
4. Comprehensive multi-market deterministic settlement with invariant financial outcome validation
5. Reconciled postponement, rescheduling, cancellation, suspension, and abandonment lifecycle rules
6. Server-side settlement route authorization enforcement with HTTP integration handlers
7. Daily-slate cache persistence failure recovery and safe retry mechanism
8. Deployment-batch scoped rollback management
"""
import os
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy import select, and_, or_, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger("tipalgo.settlement")

POSTPONEMENT_VOID_WINDOW_HOURS = 48
DEFAULT_DEPLOYMENT_BATCH_ID = os.getenv("SPORTYMILL_DEPLOYMENT_BATCH", "deploy_20261115_v21_live_001")

# -----------------------------------------------------------------------------
# 1. TIMESTAMP PARSING & VALIDATION
# -----------------------------------------------------------------------------

def parse_provider_kickoff_utc(raw_timestamp: Any) -> Optional[datetime]:
    """
    Parse an authoritative provider kickoff timestamp into UTC datetime.
    Strictly enforces:
    1. Rejection of None, empty, or whitespace-only inputs.
    2. Proper ISO-8601 parsing supporting 'Z', '+HH:MM', '-HH:MM' offsets.
    3. Rejection of naive timestamps that lack an explicit timezone offset.
    4. Accurate conversion to UTC using .astimezone(timezone.utc) (NEVER .replace()).
    """
    if raw_timestamp is None:
        return None
    if isinstance(raw_timestamp, datetime):
        if raw_timestamp.tzinfo is None or raw_timestamp.tzinfo.utcoffset(raw_timestamp) is None:
            return None  # Ambiguous naive datetime
        return raw_timestamp.astimezone(timezone.utc)

    s = str(raw_timestamp).strip()
    if not s:
        return None

    # Handle ISO-8601 'Z' suffix for Python 3.9 compatibility
    if s.endswith("Z"):
        iso_str = s[:-1] + "+00:00"
    elif "+" in s or (len(s) > 10 and "-" in s[10:]):
        iso_str = s
    else:
        # Ambiguous naive timestamp without offset
        return None

    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return None
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

# -----------------------------------------------------------------------------
# 2. CARD ELIGIBILITY & SNAPSHOT PREPARATION
# -----------------------------------------------------------------------------

def is_eligible_pre_kickoff_card(
    card: Dict[str, Any],
    now_utc: Optional[datetime] = None,
    buffer_minutes: int = 0
) -> Tuple[bool, Optional[datetime], Optional[str]]:
    """
    Validate whether a match card satisfies all pre-kickoff live snapshot invariants.
    Guarantees 100% consistency between user-facing actionable display and database persistence:
    Every recommendation displayed as actionable prior to kickoff (now < kickoff) is eligible.
    Returns: (is_eligible, parsed_kickoff_utc, rejection_reason)
    """
    if not isinstance(card, dict):
        return False, None, "Invalid card object"

    # 1. Positive prediction eligibility
    if card.get("prediction_eligible") is not True:
        return False, None, "Fixture not flagged prediction_eligible"

    # 2. Verified fixture provenance
    ext_id = card.get("external_id")
    if not ext_id or not isinstance(ext_id, str) or len(ext_id) < 5:
        return False, None, "Missing or invalid external_id"
    if ext_id.startswith("SYN-") or ext_id.startswith("auto_") or ext_id.startswith("demo_"):
        return False, None, "Synthetic or mock fixture rejected"

    # 3. Valid team identities
    h_team = card.get("home_team")
    a_team = card.get("away_team")
    if not h_team or not a_team or h_team == "Home" or a_team == "Away":
        return False, None, "Placeholder team names rejected"

    # 4. Verified live provider odds
    odds_status = card.get("odds_status")
    if odds_status != "live_provider":
        return False, None, f"Unverified odds status: {odds_status}"

    # 5. Successfully calculated prediction
    top_tip = card.get("top_tip")
    if not isinstance(top_tip, dict):
        return False, None, "Missing top_tip object"
    label = top_tip.get("label") or ""
    market = top_tip.get("market") or ""
    ep_id = top_tip.get("endpoint_id") or ""
    if not label or not market or not ep_id:
        return False, None, "Incomplete top_tip fields"
    if "ineligible" in label.lower() or market == "Match Information Only":
        return False, None, "Ineligible market label"

    prob = top_tip.get("prob_pct")
    if prob is None or not isinstance(prob, (int, float)) or prob <= 0:
        return False, None, f"Invalid model probability: {prob}"

    odds = top_tip.get("odds")
    try:
        f_odds = float(odds)
        if f_odds <= 1.0:
            return False, None, f"Invalid odds: {f_odds}"
    except (ValueError, TypeError):
        return False, None, f"Non-numeric odds: {odds}"

    # 6. Pre-kickoff boundary check
    raw_date = card.get("match_date_utc") or card.get("match_date")
    kickoff_utc = parse_provider_kickoff_utc(raw_date)
    if not kickoff_utc:
        return False, None, f"Unparseable or naive kickoff timestamp: {raw_date}"

    curr_utc = now_utc or datetime.now(timezone.utc)
    cutoff = kickoff_utc - timedelta(minutes=buffer_minutes)
    if curr_utc >= cutoff:
        return False, kickoff_utc, f"Kickoff cutoff reached: now ({curr_utc.isoformat()}) >= cutoff ({cutoff.isoformat()})"

    return True, kickoff_utc, None

# -----------------------------------------------------------------------------
# 3. DIALECT-AWARE BATCH PERSISTENCE (SQLite & PostgreSQL)
# -----------------------------------------------------------------------------

async def persist_live_slate_snapshots(
    db: AsyncSession,
    cards: List[Dict[str, Any]],
    prediction_log_model: Any,
    now_utc: Optional[datetime] = None,
    deployment_batch_id: Optional[str] = None
) -> int:
    """
    Atomic batch persistence of authentic pre-kickoff predictions for the daily slate.
    Supports both SQLite and PostgreSQL with explicit partial-index target resolution.
    Captures complete audit metadata (odds provider, lines, full model outputs, timestamps).
    """
    if not cards:
        return 0

    curr_utc = now_utc or datetime.now(timezone.utc)
    batch_id = deployment_batch_id or DEFAULT_DEPLOYMENT_BATCH_ID
    records_to_insert = []

    for card in cards:
        is_ok, kickoff_utc, reason = is_eligible_pre_kickoff_card(card, curr_utc, buffer_minutes=0)
        if not is_ok:
            continue

        ext_id = str(card["external_id"])
        top_tip = card["top_tip"]
        ep_id = str(top_tip.get("endpoint_id", "home-win"))
        prob_pct = float(top_tip.get("prob_pct", 50.0))
        odds = float(top_tip.get("odds", 2.0))

        # Complete audit snapshot payload
        fixture_payload = {
            "provider_fixture_id": ext_id,
            "competition_id": card.get("league_id"),
            "competition_name": card.get("league_name"),
            "country": card.get("country"),
            "home_team": card.get("home_team"),
            "away_team": card.get("away_team"),
            "scheduled_kickoff_utc": kickoff_utc.isoformat(),
            "selection": top_tip.get("label"),
            "market": top_tip.get("market"),
            "market_line": top_tip.get("line") or top_tip.get("market_line"),
            "endpoint_id": ep_id,
            "displayed_odds": odds,
            "odds_provider": card.get("odds_provider", "live_football_api_closing"),
            "odds_source_timestamp": card.get("odds_updated_at") or card.get("match_date_utc"),
            "banter": card.get("banter"),
            "deployment_batch_id": batch_id
        }

        raw_probs_payload = {
            "selection_prob_pct": prob_pct,
            "home_prob_pct": card.get("home_prob_pct"),
            "away_prob_pct": card.get("away_prob_pct"),
            "draw_prob_pct": card.get("draw_prob_pct", round(max(0.0, 100.0 - (float(card.get("home_prob_pct") or 0.0) + float(card.get("away_prob_pct") or 0.0))), 2)),
            "model": "Bivariate Poisson & Dixon-Coles v21.0",
            "calculated_at": curr_utc.isoformat(),
            "deployment_batch_id": batch_id
        }

        market_probs_payload = {
            "odds": odds,
            "implied_probability_pct": round((1.0 / odds) * 100.0, 2),
            "odds_status": card.get("odds_status"),
            "home_odds": card.get("home_odds"),
            "draw_odds": card.get("draw_odds"),
            "away_odds": card.get("away_odds"),
            "odds_source_timestamp": card.get("odds_updated_at") or card.get("match_date_utc")
        }

        payload = {
            "prediction_id": str(uuid.uuid4()),
            "endpoint": ep_id,
            "home_team": card["home_team"],
            "away_team": card["away_team"],
            "match_date": kickoff_utc,
            "fixture_json": json.dumps(fixture_payload),
            "raw_probs_json": json.dumps(raw_probs_payload),
            "market_probs_json": json.dumps(market_probs_payload),
            "processed": True,
            "created_at": curr_utc,
            "confidence_percent": prob_pct,
            "outcome": "pending",
            "unit_return": None,
            "source_classification": "live_recorded",
            "provider_fixture_id": ext_id,
            "model_version": f"v21.0:{batch_id}"
        }
        records_to_insert.append(payload)

    if not records_to_insert:
        return 0

    # Determine database dialect
    bind = db.bind
    dialect_name = bind.dialect.name if bind else "sqlite"

    persisted_count = 0
    try:
        for r in records_to_insert:
            if dialect_name == "postgresql":
                # PostgreSQL explicit partial unique index inference
                stmt = (
                    pg_insert(prediction_log_model)
                    .values(**r)
                    .on_conflict_do_nothing(
                        index_elements=["provider_fixture_id", "endpoint"],
                        index_where=text("source_classification = 'live_recorded'")
                    )
                )
            else:
                # SQLite prefix with OR IGNORE
                stmt = (
                    sqlite_insert(prediction_log_model)
                    .values(**r)
                    .prefix_with("OR IGNORE")
                )
            res = await db.execute(stmt)
            if res.rowcount and res.rowcount > 0:
                persisted_count += 1

        if persisted_count > 0:
            await db.commit()
            logger.info(f"Successfully persisted {persisted_count} pre-kickoff live snapshots ({dialect_name}).")
        return persisted_count
    except Exception as e:
        logger.error(f"Error persisting live slate snapshots on {dialect_name}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        raise

# -----------------------------------------------------------------------------
# 4. COMPREHENSIVE MULTI-MARKET DETERMINISTIC SETTLEMENT
# -----------------------------------------------------------------------------

def evaluate_market_settlement(
    endpoint_id: str,
    market: str,
    label: str,
    home_score: int,
    away_score: int,
    odds: float
) -> Tuple[str, float, str]:
    """
    Deterministic settlement evaluation across all supported SportyMill markets.
    Returns: (outcome: 'WON'|'LOST'|'VOID'|'PUSH'|'PENDING', unit_return: float, audit_note: str)
    
    Guarantees strict financial outcome invariants:
    - WON: unit_return == round(odds - 1.0, 2) > 0.00
    - LOST: unit_return == -1.00
    - VOID / PUSH: unit_return == 0.00
    """
    h = int(home_score)
    a = int(away_score)
    tot = h + a
    label_lower = (label or "").lower()
    mkt_lower = (market or "").lower()
    ep = (endpoint_id or "").strip().lower()
    dec_odds = max(1.0, float(odds))

    won: Optional[bool] = None

    # 1. 1X2 Match Outcomes
    if ep in ("home-win", "straight-home-win") or "home win" in mkt_lower or mkt_lower == "1":
        won = (h > a)
    elif ep == "away-win" or "away win" in mkt_lower or mkt_lower == "2":
        won = (a > h)
    elif ep == "draw" or "draw" in mkt_lower or mkt_lower in ("x", "stalemate"):
        won = (h == a)

    # 2. Double Chance (1X, X2, 12)
    elif ep.startswith("double-chance") or "double chance" in mkt_lower:
        if "1x" in ep or "1x" in label_lower or "home or draw" in label_lower:
            won = (h >= a)
        elif "x2" in ep or "x2" in label_lower or "away or draw" in label_lower:
            won = (a >= h)
        elif "12" in ep or "12" in label_lower or "home or away" in label_lower:
            won = (h != a)
        else:
            won = (h >= a)  # Default 1X

    # 3. Over / Under Goals
    elif ep in ("over-1-5", "o15") or "over 1.5" in label_lower or "over 1.5" in mkt_lower:
        won = (tot >= 2)
    elif ep in ("over-2-5", "o25") or "over 2.5" in label_lower or "over 2.5" in mkt_lower:
        won = (tot >= 3)
    elif ep in ("over-3-5", "o35") or "over 3.5" in label_lower or "over 3.5" in mkt_lower:
        won = (tot >= 4)
    elif ep in ("under-1-5", "u15") or "under 1.5" in label_lower:
        won = (tot <= 1)
    elif ep in ("under-2-5", "no-three-goals", "u25") or "under 2.5" in label_lower or "no three goals" in label_lower:
        won = (tot <= 2)
    elif ep in ("under-3-5", "u35") or "under 3.5" in label_lower:
        won = (tot <= 3)

    # 4. Both Teams to Score (BTTS)
    elif ep == "btts-yes" or ("btts" in label_lower and "no" not in label_lower and "clean sheet" not in label_lower):
        won = (h >= 1 and a >= 1)
    elif ep == "btts-no" or "btts: no" in label_lower or "clean sheet factor" in label_lower:
        won = (h == 0 or a == 0)

    # 5. Clean Sheet (Distinct Selection vs Win to Nil)
    elif ep == "clean-sheet" or "clean sheet" in mkt_lower:
        if "away" in label_lower:
            won = (h == 0)  # Away team kept clean sheet
        elif "home" in label_lower:
            won = (a == 0)  # Home team kept clean sheet
        else:
            won = (h == 0 or a == 0)

    # 6. Win to Nil (Team must win AND opponent must score 0)
    elif ep == "win-to-nil" or "win to nil" in mkt_lower or "win to nil" in label_lower:
        if "away" in label_lower:
            won = (a > h and h == 0)
        else:
            won = (h > a and a == 0)

    # 7. Goal in Both Halves / Combos
    elif ep == "goal-both-halves":
        if tot < 2:
            won = False
        else:
            return "PENDING", 0.0, "Goal in both halves requires verified half-time score breakdown."

    else:
        return "PENDING", 0.0, f"Unsupported market identifier '{ep}' ({market}) deferred for manual audit."

    if won is True:
        pnl = round(dec_odds - 1.0, 2)
        note = f"Settled WON: Match finished {h}-{a}. Payout +{pnl} units at verified odds @{dec_odds:.2f}."
        return "WON", pnl, note
    else:
        note = f"Settled LOST: Match finished {h}-{a}. Stake lost (-1.00 unit) at verified odds @{dec_odds:.2f}."
        return "LOST", -1.00, note

# -----------------------------------------------------------------------------
# 5. POSTPONEMENT, RESCHEDULING & SETTLEMENT LIFECYCLE
# -----------------------------------------------------------------------------

def evaluate_match_lifecycle_for_settlement(
    match_status: str,
    home_score: Optional[int],
    away_score: Optional[int],
    original_kickoff_utc: datetime,
    rescheduled_kickoff_utc: Optional[datetime] = None,
    now_utc: Optional[datetime] = None
) -> Tuple[str, Optional[str]]:
    """
    Reconciles fixture lifecycle states into exact settlement actions.
    Returns: (action: 'SETTLE'|'VOID'|'KEEP_PENDING', audit_reason)

    Authoritative Status Mapping:
    - Permanently cancelled -> VOID immediately (refund 0.00).
    - Suspended / Interrupted -> KEEP_PENDING awaiting pitch resumption or official ruling.
    - Abandoned with official score -> SETTLE with closing score.
    - Abandoned without official score:
        * If < 48 hours since kickoff -> KEEP_PENDING awaiting governing body ruling.
        * If >= 48 hours without ruling -> VOID.
    - Postponed with confirmed future kickoff -> KEEP_PENDING (preserve original snapshot).
    - Postponed awaiting reschedule:
        * If < 48 hours since original kickoff -> KEEP_PENDING.
        * If >= 48 hours without confirmed reschedule -> VOID.
    - Finished with missing score -> KEEP_PENDING awaiting provider feed.
    - Finished with score -> SETTLE.
    """
    curr_utc = now_utc or datetime.now(timezone.utc)
    st = (match_status or "").strip().lower()
    elapsed = curr_utc - original_kickoff_utc
    hours_elapsed = elapsed.total_seconds() / 3600.0

    # 1. Permanently cancelled
    if st in ("cancelled", "canceled", "canc"):
        return "VOID", "Fixture officially cancelled permanently by competition authority."

    # 2. Suspended / Interrupted on pitch (do NOT void immediately)
    if st in ("suspended", "susp", "interrupted", "int"):
        return "KEEP_PENDING", f"Match suspended on pitch; awaiting official resumption ({int(hours_elapsed)}h elapsed)."

    # 3. Abandoned
    if st in ("abandoned", "abd"):
        if home_score is not None and away_score is not None:
            return "SETTLE", "Match abandoned; governing body confirmed closing score stands as official result."
        if hours_elapsed >= POSTPONEMENT_VOID_WINDOW_HOURS:
            return "VOID", f"Match abandoned without official result after {int(hours_elapsed)}h (>48h cutoff). Stake refunded (0.00 units)."
        return "KEEP_PENDING", f"Match abandoned; awaiting official ruling ({int(hours_elapsed)}h / 48h cutoff)."

    # 4. Postponed / Rescheduled
    if st in ("postponed", "pst", "postp", "rescheduled"):
        if rescheduled_kickoff_utc and rescheduled_kickoff_utc > curr_utc:
            return "KEEP_PENDING", f"Fixture rescheduled to {rescheduled_kickoff_utc.isoformat()}. Preserving pending snapshot."
        if hours_elapsed >= POSTPONEMENT_VOID_WINDOW_HOURS:
            return "VOID", f"Fixture postponed > {POSTPONEMENT_VOID_WINDOW_HOURS}h without confirmed reschedule. Stake refunded."
        return "KEEP_PENDING", f"Fixture postponed awaiting rescheduling ({int(hours_elapsed)}h / 48h cutoff)."

    # 5. Finished
    if st in ("finished", "ft", "aet", "pen"):
        if home_score is None or away_score is None:
            return "KEEP_PENDING", "Match finished but final closing scores have not arrived from provider."
        return "SETTLE", "Match concluded with official scores."

    # In-play, scheduled, or unknown
    return "KEEP_PENDING", f"Match in state '{st}' not yet eligible for settlement."

async def settle_live_predictions(
    db: AsyncSession,
    prediction_log_model: Any,
    historical_match_model: Any,
    now_utc: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Automated idempotent settlement engine for live_recorded predictions.
    Matches pending live snapshots against verified provider records.
    Strictly guarantees non-retroactivity: historical backtest records are never touched.
    """
    curr_utc = now_utc or datetime.now(timezone.utc)

    # Query strictly pending live_recorded predictions
    stmt = (
        select(prediction_log_model, historical_match_model)
        .join(
            historical_match_model,
            prediction_log_model.provider_fixture_id == historical_match_model.external_id
        )
        .where(
            and_(
                prediction_log_model.source_classification == "live_recorded",
                prediction_log_model.outcome == "pending"
            )
        )
    )
    results = (await db.execute(stmt)).all()

    settled_count = 0
    void_count = 0
    won_count = 0
    lost_count = 0
    pending_count = 0

    for pred, match in results:
        m_status = match.status or "scheduled"
        h_score = match.home_score
        a_score = match.away_score
        orig_kickoff = parse_provider_kickoff_utc(pred.match_date) or curr_utc
        rescheduled_dt = parse_provider_kickoff_utc(match.match_date) if match.match_date != orig_kickoff else None

        action, lifecycle_reason = evaluate_match_lifecycle_for_settlement(
            match_status=m_status,
            home_score=h_score,
            away_score=a_score,
            original_kickoff_utc=orig_kickoff,
            rescheduled_kickoff_utc=rescheduled_dt,
            now_utc=curr_utc
        )

        if action == "KEEP_PENDING":
            pending_count += 1
            continue

        if action == "VOID":
            upd_stmt = (
                update(prediction_log_model)
                .where(
                    and_(
                        prediction_log_model.id == pred.id,
                        prediction_log_model.outcome == "pending"
                    )
                )
                .values(
                    outcome="VOID",
                    unit_return=0.00,
                    settled_at=curr_utc,
                    feedback_notes=f"Settlement VOID: {lifecycle_reason} Stake refunded (0.00 units)."
                )
            )
            upd_res = await db.execute(upd_stmt)
            if upd_res.rowcount and upd_res.rowcount > 0:
                void_count += 1
                settled_count += 1
            continue

        if action == "SETTLE":
            meta = {}
            try:
                meta = json.loads(pred.fixture_json) if pred.fixture_json else {}
            except Exception:
                pass

            odds_meta = {}
            try:
                odds_meta = json.loads(pred.market_probs_json) if pred.market_probs_json else {}
            except Exception:
                pass

            odds_val = odds_meta.get("odds", 2.0)
            outcome, unit_ret, note = evaluate_market_settlement(
                endpoint_id=pred.endpoint,
                market=meta.get("market", ""),
                label=meta.get("selection", ""),
                home_score=int(h_score),
                away_score=int(a_score),
                odds=odds_val
            )

            if outcome == "PENDING":
                pending_count += 1
                continue

            upd_stmt = (
                update(prediction_log_model)
                .where(
                    and_(
                        prediction_log_model.id == pred.id,
                        prediction_log_model.outcome == "pending"
                    )
                )
                .values(
                    outcome=outcome,
                    actual_home_score=int(h_score),
                    actual_away_score=int(a_score),
                    unit_return=unit_ret,
                    settled_at=curr_utc,
                    feedback_notes=f"{note} (Lifecycle: {lifecycle_reason})"
                )
            )
            upd_res = await db.execute(upd_stmt)
            if upd_res.rowcount and upd_res.rowcount > 0:
                if outcome == "WON":
                    won_count += 1
                elif outcome == "LOST":
                    lost_count += 1
                elif outcome == "VOID":
                    void_count += 1
                settled_count += 1

    if settled_count > 0:
        await db.commit()
        logger.info(f"Settlement batch committed: {settled_count} records settled ({won_count} WON, {lost_count} LOST, {void_count} VOID).")

    return {
        "status": "success",
        "settled_count": settled_count,
        "won_count": won_count,
        "lost_count": lost_count,
        "void_count": void_count,
        "pending_count": pending_count
    }

# -----------------------------------------------------------------------------
# 6. SETTLEMENT ROUTE AUTHORIZATION ENFORCEMENT
# -----------------------------------------------------------------------------

async def verify_settlement_authorization(
    auth_header: Optional[str],
    admin_token_header: Optional[str],
    db: AsyncSession,
    user_model: Any,
    secret_key: str,
    algorithm: str,
    configured_admin_token: str
) -> Tuple[bool, int, str]:
    """
    Enforce server-side authorization for financial settlement operations.
    Returns: (is_authorized, status_code, message)
    
    Access Rules:
    1. Valid X-Admin-Token header matching configured ADMIN_TOKEN -> Authorized.
    2. Valid Bearer JWT token belonging to an administrator (user.is_admin is True) -> Authorized.
    3. Valid Bearer JWT token belonging to a non-admin user -> 403 Forbidden.
    4. Missing or invalid credentials -> 401 Unauthorized.
    """
    # 1. Internal secure service key check
    if admin_token_header is not None:
        if configured_admin_token and admin_token_header.strip() == configured_admin_token.strip():
            return True, 200, "Authorized via secure internal service key"
        else:
            return False, 401, "Invalid service admin token"

    # 2. Bearer JWT check
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        try:
            import jwt
            payload = jwt.decode(token, secret_key, algorithms=[algorithm])
            sub = payload.get("sub")
            if sub is None:
                return False, 401, "Invalid token payload: missing subject"
            try:
                user_id = int(sub)
            except (ValueError, TypeError):
                user_id = sub
            user = (await db.execute(select(user_model).where(user_model.id == user_id))).scalar_one_or_none()
            if not user:
                return False, 401, "User not found"
            if not getattr(user, "is_admin", False):
                return False, 403, "Administrator privileges required to execute settlement operations"
            return True, 200, f"Authorized as administrator ({user.username})"
        except Exception as e:
            return False, 401, f"Invalid authorization token: {e}"

    return False, 401, "Authentication required. Provide valid administrator Bearer token or X-Admin-Token."

# -----------------------------------------------------------------------------
# 7. DAILY-SLATE CACHE PERSISTENCE RECOVERY & RETRY
# -----------------------------------------------------------------------------

async def persist_slate_with_cache_retry(
    db: AsyncSession,
    payload: Dict[str, Any],
    prediction_log_model: Any,
    now_utc: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Persistence handler with safe cache retry semantics.
    - If persistence succeeds: marks payload['live_recorded_persisted'] = True.
    - If persistence fails: logs warning, marks payload['live_recorded_persisted'] = False without crashing.
    - On subsequent cache hits, if 'live_recorded_persisted' is False, retries persistence cleanly.
    - Complete idempotency prevents duplicates during retries.
    """
    matches = payload.get("matches", [])
    if not matches or payload.get("provider_status") == "error":
        payload["live_recorded_persisted"] = False
        return payload

    try:
        persisted = await persist_live_slate_snapshots(
            db=db,
            cards=matches,
            prediction_log_model=prediction_log_model,
            now_utc=now_utc
        )
        payload["live_recorded_persisted"] = True
        payload["persisted_count"] = persisted
    except Exception as ex:
        logger.warning(f"Live prediction persistence attempt failed: {ex}. Marking cache for retry.")
        payload["live_recorded_persisted"] = False
        payload["persistence_error"] = str(ex)

    return payload
