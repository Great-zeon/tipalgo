import pytest
from datetime import datetime, timezone, timedelta
from main import resolve_fixture_lifecycle_state

def test_lifecycle_authoritative_finished():
    """Verify that authoritative provider status FT/finished yields 'finished'."""
    now = datetime(2026, 11, 17, 18, 0, 0, tzinfo=timezone.utc)
    # FT status with scores
    state = resolve_fixture_lifecycle_state(
        status="finished",
        provider_status="FT",
        match_date=now - timedelta(hours=2),
        home_score=2,
        away_score=1,
        now_utc=now
    )
    assert state == "finished"

    # Lowercase full_time
    state2 = resolve_fixture_lifecycle_state(
        status="live",
        provider_status="full_time",
        match_date=now - timedelta(hours=2),
        home_score=0,
        away_score=0,
        now_utc=now
    )
    assert state2 == "finished"

    # AET / PEN statuses
    assert resolve_fixture_lifecycle_state("live", "AET", now, 1, 1, now_utc=now) == "finished"
    assert resolve_fixture_lifecycle_state("live", "PEN", now, 2, 2, now_utc=now) == "finished"

def test_lifecycle_stalled_live_becomes_stale_never_finished():
    """
    CRITICAL SAFETY REQUIREMENT:
    A match running > 3.5 hours past kickoff without final confirmation
    must transition to 'stale', NEVER 'finished'.
    """
    now = datetime(2026, 11, 17, 20, 0, 0, tzinfo=timezone.utc)
    match_start = now - timedelta(hours=4) # 4 hours ago

    state = resolve_fixture_lifecycle_state(
        status="live",
        provider_status="2H",
        match_date=match_start,
        home_score=1,
        away_score=0,
        provider_updated_at=match_start + timedelta(minutes=105), # last updated 2+ hours ago
        now_utc=now
    )
    # Must NOT assume finished
    assert state == "stale"
    assert state != "finished"

def test_lifecycle_prolonged_live_with_recent_update():
    """
    If a match past 3.5h has a recent provider update (<30 min), e.g. prolonged delay/penalties,
    it remains 'live'.
    """
    now = datetime(2026, 11, 17, 20, 0, 0, tzinfo=timezone.utc)
    match_start = now - timedelta(hours=3, minutes=45) # 3h 45m ago
    recent_update = now - timedelta(minutes=10) # 10 minutes ago

    state = resolve_fixture_lifecycle_state(
        status="live",
        provider_status="ET",
        match_date=match_start,
        home_score=2,
        away_score=2,
        provider_updated_at=recent_update,
        now_utc=now
    )
    assert state == "live"

def test_lifecycle_in_play_with_scores_remains_live():
    """
    A live match with scores (e.g. 1-0 at 65 mins) must remain 'live',
    NEVER prematurely finished.
    """
    now = datetime(2026, 11, 17, 16, 15, 0, tzinfo=timezone.utc)
    match_start = now - timedelta(minutes=65)

    state = resolve_fixture_lifecycle_state(
        status="live",
        provider_status="2H",
        match_date=match_start,
        home_score=1,
        away_score=0,
        now_utc=now
    )
    assert state == "live"

def test_lifecycle_delayed_kickoff():
    """
    Scheduled kickoff > 4 hours in past with no events or scores transitions to 'delayed',
    NEVER 'finished'.
    """
    now = datetime(2026, 11, 17, 18, 0, 0, tzinfo=timezone.utc)
    match_start = now - timedelta(hours=5)

    state = resolve_fixture_lifecycle_state(
        status="scheduled",
        provider_status="NS",
        match_date=match_start,
        home_score=None,
        away_score=None,
        now_utc=now
    )
    assert state == "delayed"
    assert state != "finished"

def test_lifecycle_postponed_suspended_abandoned():
    """Preserves specific non-completed states from provider."""
    now = datetime(2026, 11, 17, 18, 0, 0, tzinfo=timezone.utc)
    match_start = now - timedelta(hours=1)

    assert resolve_fixture_lifecycle_state("scheduled", "PST", match_start, now_utc=now) == "postponed"
    assert resolve_fixture_lifecycle_state("live", "SUSP", match_start, 1, 0, now_utc=now) == "suspended"
    assert resolve_fixture_lifecycle_state("live", "ABD", match_start, 1, 1, now_utc=now) == "abandoned"
    assert resolve_fixture_lifecycle_state("scheduled", "CANC", match_start, now_utc=now) == "cancelled"

def test_lifecycle_naive_datetime_handling():
    """Handles naive datetimes safely without TypeError."""
    now = datetime(2026, 11, 17, 18, 0, 0, tzinfo=timezone.utc)
    naive_dt = datetime(2026, 11, 17, 16, 30, 0) # naive

    state = resolve_fixture_lifecycle_state(
        status="live",
        provider_status="2H",
        match_date=naive_dt,
        home_score=1,
        away_score=1,
        now_utc=now
    )
    assert state == "live"

