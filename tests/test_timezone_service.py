import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from timezone_service import (
    WAT, UTC, now_utc, now_lagos, lagos_today_str,
    parse_to_utc, utc_to_lagos, get_lagos_day_utc_bounds, format_fixture_datetime_meta
)

def test_timezone_constants():
    assert WAT.key == "Africa/Lagos"
    assert UTC == timezone.utc

def test_parse_to_utc_formats():
    # ISO with Z
    dt1 = parse_to_utc("2026-11-18T22:30:00Z")
    assert dt1 == datetime(2026, 11, 18, 22, 30, tzinfo=UTC)

    # SQLite string
    dt2 = parse_to_utc("2026-11-18 22:30:00.000000")
    assert dt2 == datetime(2026, 11, 18, 22, 30, tzinfo=UTC)

    # Naive datetime
    naive = datetime(2026, 11, 18, 22, 30)
    dt3 = parse_to_utc(naive)
    assert dt3 == datetime(2026, 11, 18, 22, 30, tzinfo=UTC)

def test_brazil_five_fixtures_conversion():
    # 1. Coritiba vs Santos: Brazil local Nov 18, 19:30 BRT (UTC-3) -> 22:30 UTC -> Nigeria Nov 18, 23:30 WAT
    meta1 = format_fixture_datetime_meta("2026-11-18 22:30:00.000000")
    assert meta1["match_date_wat"] == "2026-11-18"
    assert meta1["time_wat"] == "23:30"
    assert meta1["display_date_wat"] == "November 18, 2026"

    # 2. Remo vs Chapecoense: Brazil local Nov 18, 20:00 BRT -> 23:00 UTC -> Nigeria Nov 19, 00:00 WAT
    meta2 = format_fixture_datetime_meta("2026-11-18 23:00:00.000000")
    assert meta2["match_date_wat"] == "2026-11-19"
    assert meta2["time_wat"] == "00:00"
    assert meta2["display_date_wat"] == "November 19, 2026"

    # 3. Flamengo vs Athletico-PR: Brazil local Nov 18, 20:30 BRT -> 23:30 UTC -> Nigeria Nov 19, 00:30 WAT
    meta3 = format_fixture_datetime_meta("2026-11-18 23:30:00.000000")
    assert meta3["match_date_wat"] == "2026-11-19"
    assert meta3["time_wat"] == "00:30"
    assert meta3["display_date_wat"] == "November 19, 2026"

    # 4. Corinthians vs Botafogo: Brazil local Nov 18, 21:30 BRT -> Nov 19, 00:30 UTC -> Nigeria Nov 19, 01:30 WAT
    meta4 = format_fixture_datetime_meta("2026-11-19 00:30:00.000000")
    assert meta4["match_date_wat"] == "2026-11-19"
    assert meta4["time_wat"] == "01:30"
    assert meta4["display_date_wat"] == "November 19, 2026"

    # 5. Grêmio vs Bahia: Brazil local Nov 18, 21:30 BRT -> Nov 19, 00:30 UTC -> Nigeria Nov 19, 01:30 WAT
    meta5 = format_fixture_datetime_meta("2026-11-19 00:30:00.000000")
    assert meta5["match_date_wat"] == "2026-11-19"
    assert meta5["time_wat"] == "01:30"
    assert meta5["display_date_wat"] == "November 19, 2026"

def test_nigerian_day_utc_boundaries():
    start_18, end_18 = get_lagos_day_utc_bounds("2026-11-18")
    assert start_18 == datetime(2026, 11, 17, 23, 0, 0, tzinfo=UTC)
    assert end_18 == datetime(2026, 11, 18, 22, 59, 59, 999999, tzinfo=UTC)

    start_19, end_19 = get_lagos_day_utc_bounds("2026-11-19")
    assert start_19 == datetime(2026, 11, 18, 23, 0, 0, tzinfo=UTC)
    assert end_19 == datetime(2026, 11, 19, 22, 59, 59, 999999, tzinfo=UTC)

    # Coritiba vs Santos (22:30 UTC) is in Nov 18
    dt_coritiba = datetime(2026, 11, 18, 22, 30, 0, tzinfo=UTC)
    assert start_18 <= dt_coritiba <= end_18
    assert not (start_19 <= dt_coritiba <= end_19)

    # Remo vs Chapecoense (23:00 UTC) is in Nov 19
    dt_remo = datetime(2026, 11, 18, 23, 0, 0, tzinfo=UTC)
    assert not (start_18 <= dt_remo <= end_18)
    assert start_19 <= dt_remo <= end_19

    # Flamengo vs Athletico-PR (23:30 UTC) is in Nov 19
    dt_fla = datetime(2026, 11, 18, 23, 30, 0, tzinfo=UTC)
    assert not (start_18 <= dt_fla <= end_18)
    assert start_19 <= dt_fla <= end_19

