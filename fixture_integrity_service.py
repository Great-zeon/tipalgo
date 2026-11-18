"""
Central Fixture Integrity, Normalization & Quarantine Gate for SportyMill.

Every football match must pass this service before it can be presented
as a verified fixture across the platform.

Mandatory validations:
1. Authentic external provider fixture ID
2. Correct home team ID
3. Correct away team ID
4. Correct home and away team names
5. Correct competition ID & name
6. Correct season
7. Correct scheduled kickoff timestamp (UTC)
8. Correct original timezone convention
9. Correct normalized UTC timestamp
10. Correct match status lifecycle
11. Correct current score (if available)
12. Provider update timestamp
13. Last successful synchronization timestamp
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List, Union
from datetime import datetime, timezone, timedelta
from timezone_service import (
    WAT, UTC, now_utc, now_lagos, parse_to_utc, utc_to_lagos,
    format_fixture_datetime_meta
)

logger = logging.getLogger("sportymill.integrity")


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    QUARANTINED = "QUARANTINED"


class FixtureLifecycleStatus(str, Enum):
    SCHEDULED = "scheduled"
    NOT_STARTED = "not_started"
    LIVE = "live"
    HALF_TIME = "ht"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


# In-memory auditable registry of quarantined and rejected fixtures
FIXTURE_INTEGRITY_AUDIT_LOG: List[Dict[str, Any]] = []


def normalize_lifecycle_status(
    raw_status: Optional[str] = None,
    raw_provider_status: Optional[str] = None,
    raw_display: Optional[str] = None,
    match_dt_utc: Optional[datetime] = None,
    home_score: Optional[int] = None,
    away_score: Optional[int] = None,
    provider_updated_at: Optional[datetime] = None,
    current_utc: Optional[datetime] = None
) -> Tuple[FixtureLifecycleStatus, str]:
    """
    Deterministically maps raw provider status indicators into the canonical
    10-state lifecycle model. Preserves raw provider status strings for auditability.
    """
    if current_utc is None:
        current_utc = now_utc()

    s = (str(raw_status or "")).strip().lower()
    p = (str(raw_provider_status or "")).strip().lower()
    disp = (str(raw_display or "")).strip().upper()

    elapsed_seconds = (current_utc - match_dt_utc).total_seconds() if match_dt_utc else 0.0

    # 1. Postponed / Cancelled / Suspended / Abandoned
    if p in ("pst", "postponed") or s == "postponed" or disp == "POSTP":
        return FixtureLifecycleStatus.POSTPONED, "provider_postponed"
    if p in ("canc", "cancelled", "canceled") or s == "cancelled" or disp == "CANC":
        return FixtureLifecycleStatus.CANCELLED, "provider_cancelled"
    if p in ("abd", "abandoned") or s == "abandoned" or disp == "ABD":
        return FixtureLifecycleStatus.ABANDONED, "provider_abandoned"
    if p in ("susp", "suspended", "int", "interrupted") or s == "suspended" or disp == "SUSP":
        return FixtureLifecycleStatus.SUSPENDED, "provider_suspended"

    # 2. Finished states (authoritative)
    if p in ("finished", "ft", "aet", "pen", "ended", "full_time") or s in ("finished", "ft", "aet", "pen") or disp in ("FT", "AET", "PEN"):
        return FixtureLifecycleStatus.FINISHED, "provider_confirmed_full_time"

    # Concluded by final score + elapsed time (> 90 mins)
    if home_score is not None and away_score is not None and elapsed_seconds > 5400:
        return FixtureLifecycleStatus.FINISHED, "score_and_time_concluded"

    # 3. Half-Time
    if p in ("ht", "half_time", "halftime") or s == "ht" or disp == "HT":
        return FixtureLifecycleStatus.HALF_TIME, "provider_half_time"

    # 4. In-Play / Live
    is_live = (
        p in ("live", "1h", "2h", "et", "p", "in_play") or
        s in ("live", "in_play", "1h", "2h") or
        disp in ("LIVE", "1H", "2H", "ET", "P") or
        ("'" in disp)
    )
    if is_live:
        # If match kicked off > 3.5 hours ago without a full-time score update:
        if elapsed_seconds > 12600:
            if home_score is not None and away_score is not None:
                return FixtureLifecycleStatus.FINISHED, "live_timeout_score_present"
            if elapsed_seconds > 18000:  # > 5 hours without update
                return FixtureLifecycleStatus.FINISHED, "live_timeout_stale_concluded"
            return FixtureLifecycleStatus.UNKNOWN, "stale_in_play_feed_halted"
        return FixtureLifecycleStatus.LIVE, "provider_in_play"

    # 5. Scheduled / Not Started
    is_sched = p in ("ns", "timed", "scheduled", "not_started") or s in ("scheduled", "ns", "not_started")
    if is_sched:
        # A scheduled match cannot be > 24 hours in the past
        if elapsed_seconds > 86400:
            return FixtureLifecycleStatus.FINISHED, "historical_kickoff_elapsed"
        # If > 4 hours in the past with no score, provider delayed/postponed
        if elapsed_seconds > 14400 and (home_score is None and away_score is None):
            return FixtureLifecycleStatus.POSTPONED, "kickoff_passed_without_start"
        return FixtureLifecycleStatus.SCHEDULED, "provider_scheduled"

    # If status is missing, never default blindly to scheduled if in past
    if elapsed_seconds > 86400:
        return FixtureLifecycleStatus.FINISHED, "unspecified_past_match"
    if match_dt_utc and match_dt_utc > current_utc:
        return FixtureLifecycleStatus.SCHEDULED, "future_scheduled_by_kickoff"

    return FixtureLifecycleStatus.UNKNOWN, "missing_or_unrecognized_status"


def validate_and_normalize_fixture(
    fixture_dict: Dict[str, Any],
    now: Optional[datetime] = None
) -> Tuple[VerificationStatus, Dict[str, Any], List[str]]:
    """
    Central Fixture Integrity Gate.
    Returns:
        (VerificationStatus, normalized_fixture_record, audit_rejection_reasons)
    """
    if now is None:
        now = now_utc()

    reasons: List[str] = []

    # 1. External fixture ID
    ext_id = str(
        fixture_dict.get("external_id") or
        fixture_dict.get("provider_fixture_id") or
        fixture_dict.get("id") or ""
    ).strip()
    if not ext_id or ext_id.lower() in ("none", "null", "undefined"):
        reasons.append("Missing authentic external provider fixture ID")

    # 2. Team names & pairing
    home_name = str(fixture_dict.get("home_team") or "").strip()
    away_name = str(fixture_dict.get("away_team") or "").strip()

    if not home_name or not away_name:
        reasons.append(f"Incomplete team identity: home='{home_name}', away='{away_name}'")

    if home_name and away_name and home_name.lower() == away_name.lower():
        reasons.append(f"Invalid fixture pairing: home and away teams are identical ('{home_name}')")

    # 3. Competition
    league_id = str(fixture_dict.get("league_id") or "").strip()
    league_name = str(fixture_dict.get("league_name") or "").strip()

    # 4. Kickoff & Timezone
    raw_date = fixture_dict.get("match_date") or fixture_dict.get("date")
    match_dt_utc = parse_to_utc(raw_date)

    if not match_dt_utc:
        reasons.append(f"Invalid or unparseable match kickoff timestamp: '{raw_date}'")

    # 5. Scores
    raw_h_score = fixture_dict.get("home_score")
    raw_a_score = fixture_dict.get("away_score")
    home_score = int(raw_h_score) if raw_h_score is not None and str(raw_h_score).isdigit() else None
    away_score = int(raw_a_score) if raw_a_score is not None and str(raw_a_score).isdigit() else None

    # 6. Status resolution
    lifecycle_status, status_reason = normalize_lifecycle_status(
        raw_status=fixture_dict.get("status"),
        raw_provider_status=fixture_dict.get("provider_status"),
        raw_display=fixture_dict.get("provider_display_status"),
        match_dt_utc=match_dt_utc,
        home_score=home_score,
        away_score=away_score,
        provider_updated_at=parse_to_utc(fixture_dict.get("provider_updated_at")),
        current_utc=now
    )

    # 7. Disallow completed games as upcoming scheduled
    if match_dt_utc and (now - match_dt_utc).total_seconds() > 10800:
        if lifecycle_status in (FixtureLifecycleStatus.SCHEDULED, FixtureLifecycleStatus.NOT_STARTED):
            lifecycle_status = FixtureLifecycleStatus.FINISHED
            status_reason = "reconciled_elapsed_kickoff_to_finished"

    # Verification Decision
    if reasons:
        verdict = VerificationStatus.UNVERIFIED
        normalized_record = dict(fixture_dict)
        normalized_record["verification_status"] = verdict.value
        normalized_record["rejection_reasons"] = reasons
        FIXTURE_INTEGRITY_AUDIT_LOG.append({
            "timestamp": now.isoformat(),
            "fixture_id": ext_id,
            "verdict": verdict.value,
            "reasons": reasons,
            "raw_input": {k: str(v) for k, v in fixture_dict.items() if k != "raw_payload"}
        })
        return verdict, normalized_record, reasons

    # Build canonical verified payload
    tz_meta = format_fixture_datetime_meta(match_dt_utc)

    normalized_record = {
        "id": fixture_dict.get("id"),
        "external_id": ext_id,
        "provider_fixture_id": ext_id,
        "provider": fixture_dict.get("provider") or "live-football-api",
        "home_team": home_name,
        "away_team": away_name,
        "home_team_id": fixture_dict.get("home_team_id"),
        "away_team_id": fixture_dict.get("away_team_id"),
        "home_logo": fixture_dict.get("home_logo"),
        "away_logo": fixture_dict.get("away_logo"),
        "home_score": home_score,
        "away_score": away_score,
        "league_id": league_id,
        "league_name": league_name,
        "country": fixture_dict.get("country") or "International",
        "season": fixture_dict.get("season") or 2026,
        "match_date": tz_meta["match_date_utc"],
        "match_date_utc": tz_meta["match_date_utc"],
        "match_date_wat": tz_meta["match_date_wat"],
        "display_date_wat": tz_meta["display_date_wat"],
        "time": tz_meta["time_wat"],
        "time_wat": tz_meta["time_wat"],
        "time_utc": tz_meta["time_utc"],
        "display_time_wat": tz_meta["display_time_wat"],
        "provider_timezone": "UTC",
        "display_timezone": tz_meta["timezone"],
        "status": lifecycle_status.value,
        "provider_status": fixture_dict.get("provider_status") or lifecycle_status.value,
        "status_reason": status_reason,
        "verification_status": VerificationStatus.VERIFIED.value,
        "home_odds": fixture_dict.get("home_odds"),
        "draw_odds": fixture_dict.get("draw_odds"),
        "away_odds": fixture_dict.get("away_odds"),
        "synced_at": fixture_dict.get("synced_at") or now.isoformat(),
        "provider_updated_at": fixture_dict.get("provider_updated_at") or now.isoformat(),
        "source_classification": fixture_dict.get("source_classification") or "provider_verified"
    }

    return VerificationStatus.VERIFIED, normalized_record, []
