import pytest
from datetime import datetime, timezone
from fixture_integrity_service import (
    validate_and_normalize_fixture,
    normalize_lifecycle_status,
    VerificationStatus,
    FixtureLifecycleStatus,
    FIXTURE_INTEGRITY_AUDIT_LOG
)
from timezone_service import WAT, UTC, format_fixture_datetime_meta


def test_missing_external_id_rejected():
    invalid_fixture = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "match_date": "2026-11-18 20:00:00"
    }
    verdict, record, reasons = validate_and_normalize_fixture(invalid_fixture)
    assert verdict == VerificationStatus.UNVERIFIED
    assert any("external provider fixture ID" in r for r in reasons)


def test_identical_teams_rejected():
    invalid_fixture = {
        "external_id": "ext_12345",
        "home_team": "Liverpool",
        "away_team": "Liverpool",
        "match_date": "2026-11-18 20:00:00"
    }
    verdict, record, reasons = validate_and_normalize_fixture(invalid_fixture)
    assert verdict == VerificationStatus.UNVERIFIED
    assert any("identical" in r for r in reasons)


def test_valid_fixture_normalization_and_timezone():
    valid_fixture = {
        "external_id": "2nvweq9lcl19k7nv0qwunsx78",
        "home_team": "Coritiba",
        "away_team": "Santos",
        "league_id": "BSA",
        "league_name": "Brazil Serie A",
        "match_date": "2026-11-18 22:30:00",
        "status": "scheduled",
        "provider_status": "scheduled"
    }
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    verdict, record, reasons = validate_and_normalize_fixture(valid_fixture, now=now)
    assert verdict == VerificationStatus.VERIFIED
    assert record["time_wat"] == "23:30"
    assert record["match_date_wat"] == "2026-11-18"
    assert record["display_date_wat"] == "November 18, 2026"
    assert record["status"] == "scheduled"
    assert record["display_timezone"] == "Africa/Lagos (WAT, UTC+1)"


def test_midnight_rollover_brazil():
    # Remo vs Chapecoense: Nov 18, 23:00 UTC -> Nov 19, 00:00 WAT
    remo_fixture = {
        "external_id": "remo_chap_123",
        "home_team": "Remo",
        "away_team": "Chapecoense",
        "league_id": "BSA",
        "match_date": "2026-11-18 23:00:00",
        "status": "scheduled"
    }
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    verdict, record, reasons = validate_and_normalize_fixture(remo_fixture, now=now)
    assert verdict == VerificationStatus.VERIFIED
    assert record["time_wat"] == "00:00"
    assert record["match_date_wat"] == "2026-11-19"
    assert record["display_date_wat"] == "November 19, 2026"


def test_completed_match_never_scheduled():
    # Match kicked off 5 hours ago
    past_fixture = {
        "external_id": "concacaf_us_haiti",
        "home_team": "United States",
        "away_team": "Haiti",
        "league_id": "UNL",
        "match_date": "2026-11-18 10:00:00",
        "status": "scheduled",
        "home_score": 3,
        "away_score": 1
    }
    now = datetime(2026, 11, 18, 17, 0, tzinfo=UTC)
    verdict, record, reasons = validate_and_normalize_fixture(past_fixture, now=now)
    assert verdict == VerificationStatus.VERIFIED
    # Lifecycle must classify as finished, NEVER scheduled
    assert record["status"] == FixtureLifecycleStatus.FINISHED.value


def test_lifecycle_status_mapping():
    now = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
    kickoff = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)

    # In play
    st_live, _ = normalize_lifecycle_status(raw_provider_status="1H", match_dt_utc=kickoff, current_utc=now)
    assert st_live == FixtureLifecycleStatus.LIVE

    # Half time
    st_ht, _ = normalize_lifecycle_status(raw_provider_status="HT", match_dt_utc=kickoff, current_utc=now)
    assert st_ht == FixtureLifecycleStatus.HALF_TIME

    # Full time
    st_ft, _ = normalize_lifecycle_status(raw_provider_status="FT", match_dt_utc=kickoff, current_utc=now)
    assert st_ft == FixtureLifecycleStatus.FINISHED

    # Postponed
    st_pst, _ = normalize_lifecycle_status(raw_provider_status="PST", match_dt_utc=kickoff, current_utc=now)
    assert st_pst == FixtureLifecycleStatus.POSTPONED

    # Cancelled
    st_canc, _ = normalize_lifecycle_status(raw_provider_status="CANC", match_dt_utc=kickoff, current_utc=now)
    assert st_canc == FixtureLifecycleStatus.CANCELLED

    # Abandoned
    st_abd, _ = normalize_lifecycle_status(raw_provider_status="ABD", match_dt_utc=kickoff, current_utc=now)
    assert st_abd == FixtureLifecycleStatus.ABANDONED

    # Suspended
    st_susp, _ = normalize_lifecycle_status(raw_provider_status="SUSP", match_dt_utc=kickoff, current_utc=now)
    assert st_susp == FixtureLifecycleStatus.SUSPENDED


def test_midnight_rollover_panama():
    # Panama match kicking off at 01:30 UTC Nov 18 -> 02:30 WAT Nov 18
    panama_fixture = {
        "external_id": "pan_arabe_veraguas",
        "home_team": "Árabe Unido",
        "away_team": "Veraguas",
        "league_id": "PAN_LPF",
        "match_date": "2026-11-18 01:30:00",
        "status": "scheduled"
    }
    now = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    verdict, record, _ = validate_and_normalize_fixture(panama_fixture, now=now)
    assert verdict == VerificationStatus.VERIFIED
    assert record["time_wat"] == "02:30"
    assert record["match_date_wat"] == "2026-11-18"


def test_stale_match_past_24h_classified_finished():
    # Stale match scheduled 30 hours ago with no provider FT update
    stale_fixture = {
        "external_id": "stale_alg_match",
        "home_team": "CR Témouchent",
        "away_team": "USM Alger",
        "league_id": "ALG_LEAG",
        "match_date": "2026-11-16 12:00:00",
        "status": "scheduled"
    }
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    verdict, record, _ = validate_and_normalize_fixture(stale_fixture, now=now)
    assert verdict == VerificationStatus.VERIFIED
    assert record["status"] == FixtureLifecycleStatus.FINISHED.value


def test_audit_logging_of_rejected_fixtures():
    invalid = {
        "external_id": "",
        "home_team": "Team A",
        "away_team": "Team A"
    }
    initial_log_len = len(FIXTURE_INTEGRITY_AUDIT_LOG)
    verdict, _, reasons = validate_and_normalize_fixture(invalid)
    assert verdict == VerificationStatus.UNVERIFIED
    assert len(FIXTURE_INTEGRITY_AUDIT_LOG) == initial_log_len + 1
    last_entry = FIXTURE_INTEGRITY_AUDIT_LOG[-1]
    assert last_entry["verdict"] == "UNVERIFIED"

