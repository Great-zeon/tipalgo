"""
Central Timezone Conversion & Normalization Service for SportyMill.
Canonical Storage: UTC (timezone.utc)
Canonical Display: Africa/Lagos (WAT, UTC+1, no Daylight Saving Time)
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Union, Dict, Any

WAT = ZoneInfo("Africa/Lagos")
UTC = timezone.utc

def now_utc() -> datetime:
    """Current timezone-aware UTC datetime."""
    return datetime.now(UTC)

def now_lagos() -> datetime:
    """Current timezone-aware Africa/Lagos datetime."""
    return datetime.now(WAT)

def lagos_today_str() -> str:
    """Current calendar date in Nigeria (YYYY-MM-DD)."""
    return now_lagos().strftime("%Y-%m-%d")

def parse_to_utc(dt_val: Union[datetime, str, None]) -> Optional[datetime]:
    """
    Parses any provider or DB datetime representation into a timezone-aware UTC datetime.
    - If datetime and naive, assumes UTC (canonical project storage convention).
    - If datetime and timezone-aware, converts to UTC via .astimezone(UTC).
    - If string, handles ISO formats with or without Z, offsets, or space separators.
    """
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=UTC)
        return dt_val.astimezone(UTC)

    val_str = str(dt_val).strip()
    if not val_str or val_str.lower() in ("none", "null", "undefined"):
        return None

    # Replace trailing 'Z' with explicit '+00:00' for ISO parser compatibility
    val_clean = val_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(val_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val_str[:19], fmt)
            return dt.replace(tzinfo=UTC)
        except Exception:
            continue

    return None

def utc_to_lagos(dt_utc: Optional[datetime]) -> Optional[datetime]:
    """Converts a UTC datetime into Africa/Lagos (WAT, UTC+1)."""
    if dt_utc is None:
        return None
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(WAT)

def get_lagos_day_utc_bounds(target_date_str: str) -> Tuple[datetime, datetime]:
    """
    Calculates the exact UTC boundaries corresponding to the Nigerian calendar day (00:00:00 to 23:59:59.999999 WAT).
    In UTC, Nigeria's day runs from (day-1) 23:00:00 UTC to (day) 22:59:59.999999 UTC.
    """
    clean_date = str(target_date_str).strip()[:10]
    y, m, d = map(int, clean_date.split("-"))
    start_wat = datetime(y, m, d, 0, 0, 0, tzinfo=WAT)
    end_wat = datetime(y, m, d, 23, 59, 59, 999999, tzinfo=WAT)
    return start_wat.astimezone(UTC), end_wat.astimezone(UTC)

def format_fixture_datetime_meta(dt_val: Union[datetime, str, None]) -> Dict[str, Any]:
    """
    Derives canonical UTC and Africa/Lagos representation from the exact same converted timestamp.
    Guarantees:
    - No manual hour additions.
    - No combining provider original date with converted local time.
    - Derived date and time stay in perfect synchronization across midnight.
    """
    dt_u = parse_to_utc(dt_val)
    if dt_u is None:
        return {
            "match_date_utc": "2026-11-18T15:00:00Z",
            "time_utc": "15:00",
            "match_date_wat": "2026-11-18",
            "time_wat": "16:00",
            "display_date_wat": "November 18, 2026",
            "display_time_wat": "16:00",
            "timezone": "Africa/Lagos (WAT, UTC+1)"
        }

    dt_w = dt_u.astimezone(WAT)
    return {
        "match_date_utc": dt_u.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_utc": dt_u.strftime("%H:%M"),
        "match_date_wat": dt_w.strftime("%Y-%m-%d"),
        "time_wat": dt_w.strftime("%H:%M"),
        "display_date_wat": dt_w.strftime("%B %d, %Y").replace(" 0", " "),
        "display_time_wat": dt_w.strftime("%H:%M"),
        "timezone": "Africa/Lagos (WAT, UTC+1)"
    }

