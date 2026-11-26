"""
SportyMill Team Form Integrity Engine

Authoritative, provenance-verified football form and H2H calculation engine
backed strictly by Live Football API (live-football-api.com).

Guarantees:
1. Strict Live Football API provenance (rejects seeds, ESPN, manual/unverified rows, PredictionLog).
2. Strict match validation (completed statuses only, valid integer scores, valid kickoff timestamp).
3. Strict historical kickoff boundary (match_kickoff_utc < target_kickoff_utc) & target exclusion.
4. Independent calculation of Overall (Last 5), Home-only (Last 5 Home), Away-only (Last 5 Away), and H2H.
5. Team-perspective W/D/L calculation (never swapped or duplicated).
6. Transparent partial (<5), unavailable (0), and provider error states (no silent padding/fabrication).
7. Full per-match provenance (fixture_id, opponent, score, competition, venue, kickoff UTC/WAT).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

import aiohttp
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("tipalgo.team_form_integrity")

LAGOS_TZ = ZoneInfo("Africa/Lagos")

AUTHORITATIVE_PROVIDER = "live-football-api"
ALLOWED_SOURCE_CLASSIFICATIONS = frozenset({"provider_verified"})
REJECTED_PROVIDERS = frozenset({
    "standings_seed", "seed", "manual", "unverified", "espn-public", "prediction_log"
})
REJECTED_EXTERNAL_PREFIXES = ("syn-", "auto_", "hm-gen", "ext_", "seed-", "mock-")

COMPLETED_STATUSES = frozenset({
    "ft", "finished", "aet", "pen", "after penalties", "after extra time",
    "ended", "postgame", "completed", "full time", "full-time",
})
NON_COMPLETED_STATUSES = frozenset({
    "ns", "scheduled", "not started", "pregame", "live", "1h", "2h", "ht",
    "halftime", "in_play", "in-play", "pst", "postponed", "canc", "cancelled",
    "canceled", "abd", "abandoned", "susp", "suspended", "int", "interrupted",
    "awarded", "wo", "walkover", "stale", "unknown", "delayed", "tbd", "",
})

GENERIC_PLACEHOLDER_TEAMS = frozenset({
    "", "home", "away", "home team", "away team", "tbd", "tba", "unknown", "team a", "team b"
})

_NON_SENIOR_RE = re.compile(
    r"\b(u14|u15|u16|u17|u18|u19|u20|u21|u22|u23|under[\s-]?(17|18|19|20|21|23)|youth|academy|reserves?)\b",
    re.IGNORECASE,
)

DEFAULT_LFA_BASE = "https://live-football-api.com/api/v1"
KNOWN_SUSPENDED_KEYS = {"", "9c93c02b9226681e63b4bd8b7e8170d333c09ef98718d3a8"}

# Canonical club alias equivalences shared cleanly with tipalgo normalization
_FORM_TEAM_ALIAS_MAP: Dict[str, str] = {
    "ath bilbao": "athletic club",
    "ath. bilbao": "athletic club",
    "athletic bilbao": "athletic club",
    "athletic club de bilbao": "athletic club",
    "atletico": "atletico madrid",
    "atl madrid": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "atl mineiro": "atletico mineiro",
    "atletico mg": "atletico mineiro",
    "atl. mineiro": "atletico mineiro",
    "atl nacional": "atletico nacional",
    "atl. nacional": "atletico nacional",
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "nottm forest": "nottingham forest",
    "not forest": "nottingham forest",
    "brighton and hove albion": "brighton",
    "west ham united": "west ham",
    "newcastle united": "newcastle",
    "ind. medellin": "independiente medellin",
    "medellin": "independiente medellin",
    "ind. del valle": "independiente del valle",
    "ca osasuna": "osasuna",
    "def y justicia": "defensa y justicia",
    "newells ob": "newells old boys",
    "newell s old boys": "newells old boys",
    "newells": "newells old boys",
    "barca": "barcelona",
}

_CLUB_STOP_PREFIXES = (
    "fc ", "cf ", "sc ", "ec ", "ac ", "ca ", "cd ", "ud ", "sd ", "rc ",
    "ss ", "as ", "us ", "afc ", "fk ", "sk ", "nk ", "if ", "bk ", "ik ",
    "deportivo ", "atletico ", "club ", "real ", "sporting ", "racing ",
    "olympique ", "olympiacos ",
)
_CLUB_STOP_TOKENS = frozenset({
    "fc", "cf", "sc", "ec", "ac", "ca", "cd", "ud", "sd", "rc", "ss", "as",
    "afc", "fk", "sk", "nk", "if", "bk", "ik", "club", "de", "la", "del",
    "do", "da", "dos", "das",
})


def resolve_lfa_api_key(explicit_key: Optional[str] = None) -> str:
    """Resolve the active Live Football API key from explicit value or environment."""
    candidates = [
        explicit_key,
        os.getenv("LIVE_FOOTBALL_API_KEY"),
        os.getenv("FOOTBALL_API_KEY"),
    ]
    for cand in candidates:
        val = str(cand or "").strip()
        if val and val not in KNOWN_SUSPENDED_KEYS and len(val) >= 16:
            return val
    return ""


def canon_team_keys(name: Optional[str]) -> Tuple[str, str]:
    """
    Return `(full_canon, short_canon)` for safe cross-source club identity matching.
    Accents and punctuation are normalized without dangerous substring matching.
    """
    raw = str(name or "").strip().lower()
    if not raw:
        return ("", "")
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ("", "")
    mapped = _FORM_TEAM_ALIAS_MAP.get(raw, raw)
    tokens = [t for t in mapped.split() if t and t not in _CLUB_STOP_TOKENS]
    full_canon = " ".join(tokens) if tokens else mapped
    if full_canon in _FORM_TEAM_ALIAS_MAP:
        full_canon = _FORM_TEAM_ALIAS_MAP[full_canon]
    # Short key for minor abbreviation matching only alongside league/team_id safeguards
    short_tokens = list(tokens)
    for pref in _CLUB_STOP_PREFIXES:
        p = pref.strip()
        if short_tokens and short_tokens[0] == p and len(short_tokens) > 1:
            short_tokens = short_tokens[1:]
            break
    short_canon = " ".join(short_tokens[:2]) if short_tokens else full_canon
    return (full_canon, short_canon)


def parse_to_utc_aware(
    value: Any,
    unix_ts: Optional[Any] = None,
) -> Optional[datetime]:
    """Parse datetime / ISO string / UNIX timestamp strictly into timezone-aware UTC."""
    if unix_ts is not None:
        try:
            ts_num = float(unix_ts)
            if ts_num > 1_000_000_000:
                if ts_num > 10_000_000_000:  # milliseconds
                    ts_num /= 1000.0
                return datetime.fromtimestamp(ts_num, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    raw = str(value).strip()
    if not raw:
        return None
    try:
        iso_str = raw.replace("Z", "+00:00")
        if len(iso_str) == 10 and iso_str[4] == "-" and iso_str[7] == "-":
            iso_str = f"{iso_str}T00:00:00+00:00"
        elif " " in iso_str and "T" not in iso_str:
            iso_str = iso_str.replace(" ", "T", 1)
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:len(fmt)], fmt).replace(tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def compute_team_result(team_goals: int, opp_goals: int) -> str:
    """Compute W/D/L strictly from the target team's goal perspective."""
    if team_goals > opp_goals:
        return "W"
    if team_goals < opp_goals:
        return "L"
    return "D"


def extract_status_strings(raw_status: Any, display_hint: Optional[str] = None) -> Tuple[str, str, bool]:
    """Return `(lower_status, display_label, is_live_flag)` from string or Live Football API status dict."""
    is_live = False
    disp = str(display_hint or "").strip()
    if isinstance(raw_status, dict):
        is_live = bool(raw_status.get("is_live", False))
        st_val = str(raw_status.get("status") or "").strip()
        disp_val = str(raw_status.get("display") or disp or st_val).strip()
        state_val = str(raw_status.get("state") or "").strip().lower()
        combined = (disp_val or st_val or state_val).strip().lower()
        if combined in COMPLETED_STATUSES:
            return (combined, disp_val or "FT", is_live)
        if st_val.lower() in COMPLETED_STATUSES:
            return (st_val.lower(), disp_val or "FT", is_live)
        if state_val == "postgame" and st_val.lower() not in NON_COMPLETED_STATUSES:
            return ("finished", disp_val or "FT", is_live)
        return ((st_val or disp_val or state_val).lower(), disp_val or st_val or "UNKNOWN", is_live)

    st_str = str(raw_status or "").strip()
    disp_str = disp or st_str or "FT"
    return (st_str.lower(), disp_str, is_live)


def parse_score_pair(home_score_raw: Any, away_score_raw: Any = None, score_str: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """
    Extract verified non-negative integer scores `(home_goals, away_goals)`.
    Returns None if scores are missing, None, booleans, negative, or unparseable.
    """
    if (home_score_raw is None or away_score_raw is None) and score_str:
        s_clean = str(score_str).strip()
        m = re.match(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*(?:\(.*\))?\s*$", s_clean)
        if m:
            home_score_raw = int(m.group(1))
            away_score_raw = int(m.group(2))

    if isinstance(home_score_raw, bool) or isinstance(away_score_raw, bool):
        return None
    if home_score_raw is None or away_score_raw is None:
        return None
    try:
        hs = int(str(home_score_raw).strip())
        as_ = int(str(away_score_raw).strip())
    except (TypeError, ValueError):
        return None
    if hs < 0 or as_ < 0 or hs > 50 or as_ > 50:
        return None
    return (hs, as_)


def validate_provider_historical_fixture(
    raw: Dict[str, Any],
    *,
    allow_youth: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Validate and normalize a historical fixture into canonical Live Football API schema.
    Returns None if any provenance, status, score, team, or timestamp check fails.
    """
    if not isinstance(raw, dict):
        return None

    provider = str(raw.get("provider") or AUTHORITATIVE_PROVIDER).strip().lower()
    if provider != AUTHORITATIVE_PROVIDER or provider in REJECTED_PROVIDERS:
        return None

    src_cls = str(raw.get("source_classification") or "provider_verified").strip().lower()
    if src_cls not in ALLOWED_SOURCE_CLASSIFICATIONS:
        return None

    ext_id = str(
        raw.get("external_id")
        or raw.get("provider_fixture_id")
        or raw.get("fixture_id")
        or raw.get("id")
        or ""
    ).strip()
    if not ext_id or ext_id.lower().startswith(REJECTED_EXTERNAL_PREFIXES):
        return None

    home_obj = raw.get("home") if isinstance(raw.get("home"), dict) else {}
    away_obj = raw.get("away") if isinstance(raw.get("away"), dict) else {}

    home_team = str(raw.get("home_team") or home_obj.get("name") or "").strip()
    away_team = str(raw.get("away_team") or away_obj.get("name") or "").strip()
    if (
        not home_team
        or not away_team
        or home_team.lower() in GENERIC_PLACEHOLDER_TEAMS
        or away_team.lower() in GENERIC_PLACEHOLDER_TEAMS
        or home_team.lower() == away_team.lower()
    ):
        return None

    home_team_id = str(raw.get("home_team_id") or home_obj.get("id") or "").strip() or None
    away_team_id = str(raw.get("away_team_id") or away_obj.get("id") or "").strip() or None

    # Validate completion status strictly
    st_low, st_disp, is_live = extract_status_strings(
        raw.get("status") if raw.get("status") is not None else raw.get("provider_status"),
        display_hint=raw.get("provider_status"),
    )
    if is_live or st_low in NON_COMPLETED_STATUSES or st_low not in COMPLETED_STATUSES:
        return None

    # Validate exact scores
    scores = parse_score_pair(
        raw.get("home_score") if raw.get("home_score") is not None else home_obj.get("score"),
        raw.get("away_score") if raw.get("away_score") is not None else away_obj.get("score"),
        score_str=raw.get("score") if isinstance(raw.get("score"), str) else None,
    )
    if scores is None:
        return None
    hs, as_ = scores

    # Validate timestamp -> timezone-aware UTC
    kickoff_utc = parse_to_utc_aware(
        raw.get("kickoff_utc") or raw.get("match_date") or raw.get("date"),
        unix_ts=raw.get("timestamp"),
    )
    if kickoff_utc is None:
        return None

    league_obj = raw.get("league") if isinstance(raw.get("league"), dict) else {}
    comp_name = str(
        raw.get("competition")
        or raw.get("league_name")
        or league_obj.get("name")
        or raw.get("league_id")
        or ""
    ).strip()
    country_name = str(raw.get("country") or league_obj.get("country") or "").strip()
    league_id = str(raw.get("league_id") or league_obj.get("id") or "").strip()

    if not allow_youth:
        if _NON_SENIOR_RE.search(home_team) or _NON_SENIOR_RE.search(away_team) or _NON_SENIOR_RE.search(comp_name):
            return None

    kickoff_wat = kickoff_utc.astimezone(LAGOS_TZ)
    h_full, h_short = canon_team_keys(home_team)
    a_full, a_short = canon_team_keys(away_team)

    return {
        "fixture_id": ext_id,
        "provider": AUTHORITATIVE_PROVIDER,
        "source_classification": "provider_verified",
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_full_canon": h_full,
        "home_short_canon": h_short,
        "away_full_canon": a_full,
        "away_short_canon": a_short,
        "home_logo": raw.get("home_logo") or home_obj.get("logo"),
        "away_logo": raw.get("away_logo") or away_obj.get("logo"),
        "home_score": hs,
        "away_score": as_,
        "status": "finished",
        "status_display": st_disp or "FT",
        "kickoff_utc": kickoff_utc,
        "kickoff_utc_iso": kickoff_utc.isoformat(),
        "kickoff_wat_iso": kickoff_wat.isoformat(),
        "date_display": kickoff_wat.strftime("%b %d"),
        "competition": comp_name or league_id or "Official Competition",
        "country": country_name,
        "league_id": league_id or comp_name or "INTL",
        "provider_competition_id": str(league_obj.get("id") or raw.get("provider_competition_id") or "").strip() or None,
    }


def _match_touches_team(
    rec: Dict[str, Any],
    *,
    team_id: Optional[str],
    full_canon: str,
    short_canon: str,
    league_id: Optional[str] = None,
) -> Optional[bool]:
    """
    Determine whether `rec` involves the target team and return True (home), False (away), or None.
    Prefers exact provider `team_id`, then exact `full_canon`, avoiding unsafe ambiguous substring matches.
    """
    tid = str(team_id or "").strip()
    h_id = str(rec.get("home_team_id") or "").strip()
    a_id = str(rec.get("away_team_id") or "").strip()

    if tid:
        if h_id and h_id == tid:
            return True
        if a_id and a_id == tid:
            return False
        # When both records carry explicit provider team IDs and neither matches `tid`,
        # never fall back to name matching (prevents Inter Milan vs Inter El Salvador collisions).
        if h_id and a_id:
            return None

    # Canonical name checks
    h_fc = rec.get("home_full_canon") or canon_team_keys(rec.get("home_team"))[0]
    a_fc = rec.get("away_full_canon") or canon_team_keys(rec.get("away_team"))[0]

    if full_canon:
        if h_fc == full_canon:
            return True
        if a_fc == full_canon:
            return False

    # Short canonical fallback only when sharing league context and non-single generic word
    h_sc = rec.get("home_short_canon") or canon_team_keys(rec.get("home_team"))[1]
    a_sc = rec.get("away_short_canon") or canon_team_keys(rec.get("away_team"))[1]
    rec_lg = str(rec.get("league_id") or "").strip().lower()
    req_lg = str(league_id or "").strip().lower()
    if short_canon and len(short_canon) >= 5 and (not req_lg or not rec_lg or rec_lg == req_lg):
        if h_sc == short_canon and len(h_fc.split()) <= 2:
            return True
        if a_sc == short_canon and len(a_fc.split()) <= 2:
            return False

    return None


def compute_independent_team_form(
    verified_matches: Iterable[Dict[str, Any]],
    *,
    team_name: str,
    team_id: Optional[str] = None,
    league_id: Optional[str] = None,
    venue: Optional[str] = None,  # None ("overall") | "home" | "away"
    target_kickoff_utc: Optional[datetime] = None,
    exclude_fixture_id: Optional[str] = None,
    limit: int = 5,
    provider_error: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    """
    Pure, deterministic calculator computing a single team's verified form dataset
    (General, Home-only, or Away-only) from validated Live Football API match records.

    Sorted strictly newest-to-oldest (`kickoff_utc DESC`) using only matches strictly
    before `target_kickoff_utc` and excluding `exclude_fixture_id`.
    """
    cutoff_utc = parse_to_utc_aware(target_kickoff_utc) or datetime.now(timezone.utc)
    excl_id = str(exclude_fixture_id or "").strip()
    full_c, short_c = canon_team_keys(team_name)
    req_venue = str(venue or "").strip().lower() or None

    candidates: List[Dict[str, Any]] = []
    seen_fixture_ids: Set[str] = set()
    seen_match_keys: Set[Tuple[str, str, str]] = set()

    for raw_rec in verified_matches:
        rec = raw_rec if "home_full_canon" in raw_rec else validate_provider_historical_fixture(raw_rec)
        if not rec:
            continue

        fid = str(rec["fixture_id"]).strip()
        if excl_id and fid == excl_id:
            continue
        if fid in seen_fixture_ids:
            continue

        ko_utc = rec["kickoff_utc"]
        if ko_utc >= cutoff_utc:
            continue

        side = _match_touches_team(
            rec,
            team_id=team_id,
            full_canon=full_c,
            short_canon=short_c,
            league_id=league_id,
        )
        if side is None:
            continue
        if req_venue == "home" and not side:
            continue
        if req_venue == "away" and side:
            continue

        # Dedup identical real-world game if fixture_id differs across ingestion tables
        pair = tuple(sorted([rec["home_full_canon"], rec["away_full_canon"]]))
        dedup_sig = (ko_utc.strftime("%Y-%m-%d"), pair[0], pair[1])
        if dedup_sig in seen_match_keys:
            continue

        seen_fixture_ids.add(fid)
        seen_match_keys.add(dedup_sig)

        hs = int(rec["home_score"])
        as_ = int(rec["away_score"])
        team_goals = hs if side else as_
        opp_goals = as_ if side else hs
        opp_name = rec["away_team"] if side else rec["home_team"]
        opp_id = rec["away_team_id"] if side else rec["home_team_id"]
        result_char = compute_team_result(team_goals, opp_goals)

        candidates.append({
            "fixture_id": fid,
            "opponent": opp_name,
            "opponent_id": opp_id,
            "venue": "home" if side else "away",
            "team_goals": team_goals,
            "opp_goals": opp_goals,
            "home_team": rec["home_team"],
            "away_team": rec["away_team"],
            "home_score": hs,
            "away_score": as_,
            "score": f"{hs} - {as_}",
            "team_score_display": f"{team_goals} - {opp_goals}",
            "result": result_char,
            "res": result_char,
            "opp": opp_name,
            "kickoff_utc": rec["kickoff_utc_iso"],
            "kickoff_wat": rec["kickoff_wat_iso"],
            "date": rec["kickoff_utc_iso"],
            "date_display": rec["date_display"],
            "competition": rec["competition"],
            "country": rec.get("country", ""),
            "league_id": rec.get("league_id", ""),
            "status": rec["status_display"],
            "provider": AUTHORITATIVE_PROVIDER,
            "_sort_dt": ko_utc,
        })

    # Newest first strictly by actual UTC kickoff timestamp, tie-broken deterministically by fixture_id
    candidates.sort(key=lambda item: (item["_sort_dt"], item["fixture_id"]), reverse=True)
    selected = candidates[: max(0, int(limit))]
    for item in selected:
        item.pop("_sort_dt", None)

    symbols = [item["result"] for item in selected]
    verified_count = len(symbols)
    has_err = bool(provider_error and provider_error.get("error_code"))

    if verified_count >= limit:
        status = "complete"
        label = f"{verified_count}/{limit} verified"
    elif verified_count > 0:
        status = "partial"
        label = f"{verified_count}/{limit} verified matches"
    elif has_err:
        status = "provider_error"
        label = provider_error.get("error_message") or "Form provider unavailable"
    else:
        status = "unavailable"
        label = "Recent form unavailable"

    return {
        "team": team_name,
        "team_id": str(team_id or "").strip() or None,
        "scope": req_venue or "overall",
        "symbols": symbols,
        "string": "".join(symbols),
        "verified_count": verified_count,
        "requested_count": limit,
        "status": status,
        "label": label,
        "error_code": provider_error.get("error_code") if (has_err and verified_count == 0) else None,
        "error_message": provider_error.get("error_message") if (has_err and verified_count == 0) else None,
        "provider": AUTHORITATIVE_PROVIDER,
        "matches": selected,
    }


def compute_independent_h2h(
    verified_matches: Iterable[Dict[str, Any]],
    *,
    home_team: str,
    away_team: str,
    home_team_id: Optional[str] = None,
    away_team_id: Optional[str] = None,
    target_kickoff_utc: Optional[datetime] = None,
    exclude_fixture_id: Optional[str] = None,
    limit: int = 5,
    extra_h2h_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Compute verified head-to-head clashes strictly prior to `target_kickoff_utc`
    using only Live Football API completed match records.
    """
    cutoff_utc = parse_to_utc_aware(target_kickoff_utc) or datetime.now(timezone.utc)
    excl_id = str(exclude_fixture_id or "").strip()
    h_fc, h_sc = canon_team_keys(home_team)
    a_fc, a_sc = canon_team_keys(away_team)

    combined: List[Dict[str, Any]] = []
    for rec in verified_matches:
        norm = rec if "home_full_canon" in rec else validate_provider_historical_fixture(rec)
        if norm:
            combined.append(norm)
    if extra_h2h_rows:
        for idx, row in enumerate(extra_h2h_rows):
            if not isinstance(row, dict):
                continue
            synth_input = dict(row)
            if not synth_input.get("id") and not synth_input.get("fixture_id") and not synth_input.get("external_id"):
                # Live Football API `/h2h?match_id=...` omits fixture `id` on H2H array items; construct deterministic key from match_id+date
                dt_raw = str(row.get("date") or "").strip()
                r_home = ((row.get("home") or {}).get("id") or "")
                r_away = ((row.get("away") or {}).get("id") or "")
                if dt_raw and r_home and r_away:
                    synth_input["external_id"] = f"lfa_h2h_{dt_raw}_{r_home}_{r_away}"
            synth_input.setdefault("status", "FT")
            synth_input.setdefault("provider", AUTHORITATIVE_PROVIDER)
            synth_input.setdefault("source_classification", "provider_verified")
            norm = validate_provider_historical_fixture(synth_input)
            if norm:
                combined.append(norm)

    clashes: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    seen_sigs: Set[Tuple[str, str, str]] = set()

    for rec in combined:
        fid = str(rec["fixture_id"]).strip()
        if excl_id and fid == excl_id:
            continue
        if fid in seen_ids:
            continue
        if rec["kickoff_utc"] >= cutoff_utc:
            continue

        side_home = _match_touches_team(rec, team_id=home_team_id, full_canon=h_fc, short_canon=h_sc)
        side_away = _match_touches_team(rec, team_id=away_team_id, full_canon=a_fc, short_canon=a_sc)
        if side_home is None or side_away is None or side_home == side_away:
            continue

        pair = tuple(sorted([rec["home_full_canon"], rec["away_full_canon"]]))
        sig = (rec["kickoff_utc"].strftime("%Y-%m-%d"), pair[0], pair[1])
        if sig in seen_sigs:
            continue
        seen_ids.add(fid)
        seen_sigs.add(sig)

        hs = int(rec["home_score"])
        as_ = int(rec["away_score"])
        # Target perspective: home_team vs away_team in the analyzed upcoming fixture
        target_home_goals = hs if side_home else as_
        target_away_goals = as_ if side_home else hs
        outcome = compute_team_result(target_home_goals, target_away_goals)

        clashes.append({
            "fixture_id": fid,
            "home_team": rec["home_team"],
            "away_team": rec["away_team"],
            "home_score": hs,
            "away_score": as_,
            "score": f"{hs} - {as_}",
            "target_home_goals": target_home_goals,
            "target_away_goals": target_away_goals,
            "home_scored": target_home_goals > 0,
            "away_scored": target_away_goals > 0,
            "target_scored": target_home_goals > 0,
            "result_for_home_team": outcome,
            "result": f"{rec['home_team']} {hs}-{as_} {rec['away_team']}",
            "kickoff_utc": rec["kickoff_utc_iso"],
            "kickoff_wat": rec["kickoff_wat_iso"],
            "date": rec["kickoff_utc_iso"],
            "date_display": rec["date_display"],
            "competition": rec["competition"],
            "status": rec["status_display"],
            "provider": AUTHORITATIVE_PROVIDER,
            "_sort_dt": rec["kickoff_utc"],
        })

    clashes.sort(key=lambda c: (c["_sort_dt"], c["fixture_id"]), reverse=True)
    selected = clashes[: max(0, int(limit))]
    total_goals = sum(int(c["home_score"]) + int(c["away_score"]) for c in selected)
    h_wins = sum(1 for c in selected if c["result_for_home_team"] == "W")
    draws = sum(1 for c in selected if c["result_for_home_team"] == "D")
    a_wins = sum(1 for c in selected if c["result_for_home_team"] == "L")
    for c in selected:
        c.pop("_sort_dt", None)

    return {
        "total_matches": len(selected),
        "home_wins": h_wins,
        "draws": draws,
        "away_wins": a_wins,
        "avg_goals": f"{(total_goals / len(selected)):.1f}" if selected else "N/A",
        "avg_goals_num": round(total_goals / len(selected), 2) if selected else None,
        "matches": selected,
        "provider": AUTHORITATIVE_PROVIDER,
    }


class LiveFootballHistoryClient:
    """
    Async client for Live Football API team historical matches (`/api/v1/team_matches`),
    fixture H2H (`/api/v1/h2h`), and team search (`/api/v1/team_search`).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        http_get: Optional[Callable[[str], Any]] = None,
    ):
        self.api_key = resolve_lfa_api_key(api_key)
        self.base_url = (base_url or DEFAULT_LFA_BASE).rstrip("/")
        self._http_get = http_get  # Optional test injection hook returning (status_code, json_dict_or_text)
        self.last_credits_remaining: Optional[int] = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _request_json(self, path_and_query: str, max_retries: int = 2) -> Dict[str, Any]:
        if not self.is_configured():
            return {
                "success": False,
                "status_code": 401,
                "error_code": "AUTH_FAILURE",
                "error_message": "Live Football API key is not configured or invalid.",
                "data": None,
            }

        sep = "&" if "?" in path_and_query else "?"
        url = f"{self.base_url}{path_and_query}{sep}api_key={self.api_key}"

        for attempt in range(max_retries + 1):
            try:
                if self._http_get is not None:
                    res = self._http_get(url)
                    if asyncio.iscoroutine(res):
                        res = await res
                    status_code, payload = res
                    body_text = json.dumps(payload) if isinstance(payload, dict) else str(payload or "")
                else:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=18) as resp:
                            status_code = resp.status
                            body_text = await resp.text()

                try:
                    parsed = json.loads(body_text) if body_text else {}
                except Exception:
                    parsed = None

                if status_code == 200:
                    if not isinstance(parsed, dict):
                        return {
                            "success": False,
                            "status_code": 502,
                            "error_code": "MALFORMED_PAYLOAD",
                            "error_message": "Provider returned non-JSON payload.",
                            "data": None,
                        }
                    if "credits_remaining" in parsed:
                        try:
                            self.last_credits_remaining = int(parsed["credits_remaining"])
                        except Exception:
                            pass
                    if parsed.get("success"):
                        return {
                            "success": True,
                            "status_code": 200,
                            "error_code": None,
                            "error_message": None,
                            "data": parsed.get("data"),
                            "credits_remaining": parsed.get("credits_remaining"),
                        }
                    msg = str(parsed.get("message") or "Provider returned unsuccessful status")
                    low = msg.lower()
                    if any(k in low for k in ("credit", "limit", "quota", "balance", "inactive")):
                        return {
                            "success": False,
                            "status_code": 403,
                            "error_code": "INSUFFICIENT_CREDITS",
                            "error_message": "Live Football API credits exhausted.",
                            "data": None,
                        }
                    if "key" in low or "auth" in low or "unauthorized" in low:
                        return {
                            "success": False,
                            "status_code": 401,
                            "error_code": "AUTH_FAILURE",
                            "error_message": "Live Football API authentication failed.",
                            "data": None,
                        }
                    return {
                        "success": False,
                        "status_code": 400,
                        "error_code": "PROVIDER_ERROR",
                        "error_message": msg,
                        "data": None,
                    }

                low_body = (body_text or "").lower()
                if status_code in (401, 403):
                    if any(k in low_body for k in ("credit", "limit", "quota", "balance")):
                        return {
                            "success": False,
                            "status_code": status_code,
                            "error_code": "INSUFFICIENT_CREDITS",
                            "error_message": "Live Football API credits exhausted.",
                            "data": None,
                        }
                    return {
                        "success": False,
                        "status_code": status_code,
                        "error_code": "AUTH_FAILURE",
                        "error_message": "Live Football API authentication failed.",
                        "data": None,
                    }
                if status_code == 429:
                    if attempt < max_retries:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    return {
                        "success": False,
                        "status_code": 429,
                        "error_code": "RATE_LIMITED",
                        "error_message": "Live Football API rate limit exceeded.",
                        "data": None,
                    }
                if status_code >= 500:
                    if attempt < max_retries:
                        await asyncio.sleep(0.45 * (attempt + 1))
                        continue
                    return {
                        "success": False,
                        "status_code": status_code,
                        "error_code": "PROVIDER_TIMEOUT" if status_code in (502, 504) else "PROVIDER_ERROR",
                        "error_message": f"Live Football API upstream error (HTTP {status_code}).",
                        "data": None,
                    }
                return {
                    "success": False,
                    "status_code": status_code,
                    "error_code": "PROVIDER_ERROR",
                    "error_message": f"Live Football API error (HTTP {status_code}).",
                    "data": None,
                }

            except (asyncio.TimeoutError, TimeoutError):
                if attempt < max_retries:
                    await asyncio.sleep(0.45 * (attempt + 1))
                    continue
                return {
                    "success": False,
                    "status_code": 504,
                    "error_code": "PROVIDER_TIMEOUT",
                    "error_message": "Live Football API request timed out.",
                    "data": None,
                }
            except Exception as exc:
                if attempt < max_retries:
                    await asyncio.sleep(0.45 * (attempt + 1))
                    continue
                return {
                    "success": False,
                    "status_code": 502,
                    "error_code": "PROVIDER_ERROR",
                    "error_message": f"Live Football API connection failed: {exc}",
                    "data": None,
                }

        return {
            "success": False,
            "status_code": 502,
            "error_code": "PROVIDER_ERROR",
            "error_message": "Live Football API request exhausted retries.",
            "data": None,
        }

    async def fetch_team_matches_season(self, team_id: str, season: Optional[str] = None) -> Dict[str, Any]:
        """Fetch raw team_matches for a specific team ID and optional season string."""
        tid = str(team_id or "").strip()
        if not tid:
            return {
                "success": False,
                "status_code": 400,
                "error_code": "MISSING_TEAM_ID",
                "error_message": "Missing Live Football API team_id.",
                "matches": [],
                "available_seasons": [],
                "season": None,
            }
        q = f"/team_matches?team_id={tid}"
        if season:
            q += f"&season={season}"
        res = await self._request_json(q)
        if not res["success"]:
            return {
                "success": False,
                "status_code": res["status_code"],
                "error_code": res["error_code"],
                "error_message": res["error_message"],
                "matches": [],
                "available_seasons": [],
                "season": None,
            }
        data = res.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("matches", []), list):
            return {
                "success": False,
                "status_code": 502,
                "error_code": "MALFORMED_PAYLOAD",
                "error_message": "Provider returned malformed team_matches payload.",
                "matches": [],
                "available_seasons": [],
                "season": None,
            }
        raw_matches = data.get("matches", [])
        validated: List[Dict[str, Any]] = []
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            item_copy["provider"] = AUTHORITATIVE_PROVIDER
            item_copy["source_classification"] = "provider_verified"
            norm = validate_provider_historical_fixture(item_copy)
            if norm:
                validated.append(norm)
        return {
            "success": True,
            "status_code": 200,
            "error_code": None,
            "error_message": None,
            "matches": validated,
            "raw_matches_count": len(raw_matches),
            "available_seasons": [str(s) for s in (data.get("available_seasons") or [])],
            "season": str(data.get("season") or season or ""),
            "credits_remaining": res.get("credits_remaining"),
        }

    async def fetch_team_history_with_fallback(
        self,
        team_id: str,
        *,
        team_name: str = "",
        target_kickoff_utc: Optional[datetime] = None,
        min_venue_count: int = 5,
        max_extra_seasons: int = 2,
    ) -> Dict[str, Any]:
        """
        Fetch authentic completed matches for `team_id` from `/api/v1/team_matches`,
        automatically stepping back into earlier seasons from `available_seasons` when
        the default season has fewer than `min_venue_count` overall/home/away verified results.
        """
        first = await self.fetch_team_matches_season(team_id)
        if not first["success"]:
            return first

        all_verified: List[Dict[str, Any]] = list(first["matches"])
        checked_seasons: Set[str] = {first["season"]} if first.get("season") else set()
        avail = first.get("available_seasons") or []

        def _has_full_coverage(items: List[Dict[str, Any]]) -> bool:
            if not team_name and not team_id:
                return len(items) >= 15
            gen = compute_independent_team_form(
                items, team_name=team_name, team_id=team_id, venue=None,
                target_kickoff_utc=target_kickoff_utc, limit=min_venue_count
            )
            hm = compute_independent_team_form(
                items, team_name=team_name, team_id=team_id, venue="home",
                target_kickoff_utc=target_kickoff_utc, limit=min_venue_count
            )
            aw = compute_independent_team_form(
                items, team_name=team_name, team_id=team_id, venue="away",
                target_kickoff_utc=target_kickoff_utc, limit=min_venue_count
            )
            return (
                gen["verified_count"] >= min_venue_count
                and hm["verified_count"] >= min_venue_count
                and aw["verified_count"] >= min_venue_count
            )

        if not _has_full_coverage(all_verified) and avail:
            # Determine candidate previous seasons: skip empty future seasons (e.g. "2027" when target is 2026)
            target_year = (target_kickoff_utc or datetime.now(timezone.utc)).year
            extras_checked = 0
            for cand_season in avail:
                if cand_season in checked_seasons:
                    continue
                # Skip strictly future single-year seasons e.g. 2028 when target is 2026
                if re.fullmatch(r"\d{4}", cand_season) and int(cand_season) > target_year + 1:
                    continue
                checked_seasons.add(cand_season)
                more = await self.fetch_team_matches_season(team_id, season=cand_season)
                extras_checked += 1
                if more["success"]:
                    all_verified.extend(more["matches"])
                    if _has_full_coverage(all_verified):
                        break
                elif more.get("error_code") in ("AUTH_FAILURE", "INSUFFICIENT_CREDITS"):
                    # Keep whatever season 1 already fetched, attach error only if 0 matches
                    if not all_verified:
                        return more
                    break
                if extras_checked >= max_extra_seasons:
                    break

        return {
            "success": True,
            "status_code": 200,
            "error_code": None,
            "error_message": None,
            "matches": all_verified,
            "credits_remaining": self.last_credits_remaining,
        }

    async def fetch_match_h2h(self, match_id: str) -> Dict[str, Any]:
        """Fetch `/api/v1/h2h?match_id={match_id}` for official Live Football API H2H + cross-check."""
        mid = str(match_id or "").strip()
        if not mid or not mid.isdigit():
            return {
                "success": False,
                "status_code": 400,
                "error_code": "INVALID_MATCH_ID",
                "error_message": "Numeric Live Football API match_id required for h2h.",
                "data": None,
            }
        return await self._request_json(f"/h2h?match_id={mid}")

    async def search_team_id(self, team_name: str, country: Optional[str] = None) -> Optional[str]:
        """Resolve a Live Football API numeric team ID cleanly via `/api/v1/team_search`."""
        q_name = str(team_name or "").strip()
        if not q_name:
            return None
        res = await self._request_json(f"/team_search?q={q_name}")
        if not res["success"] or not isinstance(res.get("data"), dict):
            return None
        teams = res["data"].get("teams") or []
        req_fc, _ = canon_team_keys(q_name)
        c_low = str(country or "").strip().lower()
        exact_country_match = None
        exact_name_match = None
        for t in teams:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            t_fc, _ = canon_team_keys(t.get("name"))
            t_country = str(t.get("country") or "").strip().lower()
            if t_fc == req_fc:
                if c_low and t_country == c_low:
                    exact_country_match = str(t["id"])
                    break
                if exact_name_match is None:
                    exact_name_match = str(t["id"])
        return exact_country_match or exact_name_match


class TeamFormIntegrityEngine:
    """
    Central coordinator combining:
    - Strict Live Football API PostgreSQL/SQLite verified match history cache
    - Live `/api/v1/team_matches` & `/api/v1/h2h` on-demand sync with TTL caching
    - Independent pre-kickoff calculation of Overall / Home / Away / H2H form datasets
    """

    def __init__(self, client: Optional[LiveFootballHistoryClient] = None, team_sync_ttl_seconds: int = 1800):
        self.client = client or LiveFootballHistoryClient()
        self.team_sync_ttl = max(60, int(team_sync_ttl_seconds))
        self._team_sync_ts: Dict[str, float] = {}
        self._team_errors: Dict[str, Tuple[float, Dict[str, Optional[str]]]] = {}
        self._team_memory_records: Dict[str, List[Dict[str, Any]]] = {}
        self._resolved_team_ids: Dict[str, str] = {}
        self._h2h_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._sync_locks: Dict[str, asyncio.Lock] = {}
        self._circuit_broken_until: float = 0.0
        self._circuit_error: Optional[Dict[str, Optional[str]]] = None

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._sync_locks:
            self._sync_locks[key] = asyncio.Lock()
        return self._sync_locks[key]

    def _is_circuit_open(self) -> Optional[Dict[str, Optional[str]]]:
        if time.time() < self._circuit_broken_until and self._circuit_error:
            return self._circuit_error
        return None

    def _trip_circuit_if_fatal(self, error_code: Optional[str], error_message: Optional[str]):
        if error_code in ("AUTH_FAILURE", "INSUFFICIENT_CREDITS"):
            self._circuit_broken_until = time.time() + 120.0
            self._circuit_error = {"error_code": error_code, "error_message": error_message}

    async def _load_verified_db_matches(
        self,
        db: Optional[AsyncSession],
        *,
        team_ids: Optional[Set[str]] = None,
        team_names: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query `historical_matches` STRICTLY restricted to `provider == 'live-football-api'`
        and `source_classification == 'provider_verified'`. Never reads seeded/ESPN/manual rows.
        """
        if db is None:
            return []
        from main import HistoricalMatch  # Local import avoids circular module init

        clauses = [
            HistoricalMatch.provider == AUTHORITATIVE_PROVIDER,
            HistoricalMatch.source_classification.in_(list(ALLOWED_SOURCE_CLASSIFICATIONS)),
            HistoricalMatch.home_score.isnot(None),
            HistoricalMatch.away_score.isnot(None),
        ]

        team_filters = []
        clean_ids = [str(tid).strip() for tid in (team_ids or set()) if str(tid or "").strip()]
        if clean_ids:
            team_filters.append(HistoricalMatch.home_team_id.in_(clean_ids))
            team_filters.append(HistoricalMatch.away_team_id.in_(clean_ids))
        for tname in (team_names or set()):
            tn = str(tname or "").strip()
            if tn:
                team_filters.append(HistoricalMatch.home_team.ilike(tn))
                team_filters.append(HistoricalMatch.away_team.ilike(tn))
                fc, _ = canon_team_keys(tn)
                if fc and fc != tn.lower():
                    team_filters.append(HistoricalMatch.home_team.ilike(f"%{fc.split()[0]}%"))
                    team_filters.append(HistoricalMatch.away_team.ilike(f"%{fc.split()[0]}%"))

        if team_filters:
            clauses.append(or_(*team_filters))

        stmt = select(HistoricalMatch).where(and_(*clauses)).order_by(desc(HistoricalMatch.match_date)).limit(4000)
        res = await db.execute(stmt)
        rows = res.scalars().all()

        verified: List[Dict[str, Any]] = []
        for r in rows:
            comp_name = r.league_id
            country_name = ""
            st_disp = r.provider_status or r.status
            if getattr(r, "raw_payload", None):
                try:
                    rp = json.loads(r.raw_payload) if isinstance(r.raw_payload, str) else r.raw_payload
                    if isinstance(rp, dict):
                        lg_obj = rp.get("league") if isinstance(rp.get("league"), dict) else {}
                        comp_name = lg_obj.get("name") or rp.get("competition") or comp_name
                        country_name = lg_obj.get("country") or rp.get("country") or ""
                        if not st_disp and rp.get("status"):
                            st_disp = rp.get("status")
                except Exception:
                    pass
            norm = validate_provider_historical_fixture({
                "external_id": r.external_id,
                "provider": r.provider,
                "source_classification": r.source_classification,
                "home_team": r.home_team,
                "away_team": r.away_team,
                "home_team_id": r.home_team_id,
                "away_team_id": r.away_team_id,
                "home_logo": r.home_logo,
                "away_logo": r.away_logo,
                "home_score": r.home_score,
                "away_score": r.away_score,
                "league_id": r.league_id,
                "competition": comp_name,
                "country": country_name,
                "match_date": r.match_date,
                "status": r.status,
                "provider_status": st_disp,
            })
            if norm:
                verified.append(norm)
        return verified

    async def _persist_verified_matches_to_db(
        self,
        db: Optional[AsyncSession],
        matches: List[Dict[str, Any]],
    ) -> int:
        """Persist validated Live Football API historical matches cleanly to HistoricalMatch."""
        if db is None or not matches:
            return 0
        from main import HistoricalMatch

        ext_ids = list({str(m["fixture_id"]).strip() for m in matches if m.get("fixture_id")})
        if not ext_ids:
            return 0

        existing_map: Dict[str, HistoricalMatch] = {}
        for i in range(0, len(ext_ids), 400):
            chunk = ext_ids[i:i + 400]
            res = await db.execute(select(HistoricalMatch).where(HistoricalMatch.external_id.in_(chunk)))
            for row in res.scalars().all():
                existing_map[str(row.external_id)] = row

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        written = 0
        seen_batch: Set[str] = set()
        for m in matches:
            fid = str(m["fixture_id"]).strip()
            if fid in seen_batch:
                continue
            seen_batch.add(fid)
            ko_naive = m["kickoff_utc"].astimezone(timezone.utc).replace(tzinfo=None)
            raw_meta = json.dumps({
                "league": {
                    "id": m.get("provider_competition_id"),
                    "name": m.get("competition"),
                    "country": m.get("country"),
                },
                "status": m.get("status_display", "FT"),
            })
            row = existing_map.get(fid)
            if row is not None:
                row.home_score = int(m["home_score"])
                row.away_score = int(m["away_score"])
                row.status = "finished"
                row.provider = AUTHORITATIVE_PROVIDER
                row.source_classification = "provider_verified"
                row.provider_fixture_id = fid
                row.provider_status = m.get("status_display", "FT")
                if m.get("home_team_id"):
                    row.home_team_id = str(m["home_team_id"])
                if m.get("away_team_id"):
                    row.away_team_id = str(m["away_team_id"])
                if m.get("league_id"):
                    row.league_id = str(m["league_id"])
                row.raw_payload = raw_meta
                row.provider_updated_at = now_naive
                row.synced_at = now_naive
            else:
                db.add(HistoricalMatch(
                    external_id=fid,
                    home_team=m["home_team"],
                    away_team=m["away_team"],
                    home_logo=m.get("home_logo"),
                    away_logo=m.get("away_logo"),
                    home_score=int(m["home_score"]),
                    away_score=int(m["away_score"]),
                    league_id=str(m.get("league_id") or m.get("competition") or "INTL"),
                    match_date=ko_naive,
                    status="finished",
                    source_classification="provider_verified",
                    provider=AUTHORITATIVE_PROVIDER,
                    provider_fixture_id=fid,
                    provider_competition_id=m.get("provider_competition_id"),
                    home_team_id=m.get("home_team_id"),
                    away_team_id=m.get("away_team_id"),
                    provider_status=m.get("status_display", "FT"),
                    provider_updated_at=now_naive,
                    synced_at=now_naive,
                    raw_payload=raw_meta,
                ))
            written += 1

        if written:
            try:
                await db.commit()
            except Exception as exc:
                logger.warning(f"HistoricalMatch batch commit notice: {exc}")
                await db.rollback()
                return 0
        return written

    async def sync_team_history_if_needed(
        self,
        db: Optional[AsyncSession],
        *,
        team_name: str,
        team_id: Optional[str] = None,
        country: Optional[str] = None,
        target_kickoff_utc: Optional[datetime] = None,
        force_refresh: bool = False,
        persist_to_db: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Optional[str]]]]:
        """
        Sync a team's historical matches from Live Football API `/team_matches`
        if not recently synced in memory cache, persisting validated rows to DB.
        """
        circuit = self._is_circuit_open()
        if circuit:
            return (list(self._team_memory_records.get(str(team_id or ""), [])), circuit)

        tid = str(team_id or "").strip()
        canon_key = canon_team_keys(team_name)[0]
        if not tid and canon_key in self._resolved_team_ids:
            tid = self._resolved_team_ids[canon_key]

        if not tid and team_name and self.client.is_configured():
            resolved = await self.client.search_team_id(team_name, country=country)
            if resolved:
                tid = resolved
                self._resolved_team_ids[canon_key] = resolved

        if not tid:
            return ([], {
                "error_code": "MISSING_TEAM_ID" if not self.client.is_configured() else None,
                "error_message": None,
            })

        now = time.time()
        last_ts = self._team_sync_ts.get(tid, 0.0)
        if not force_refresh and (now - last_ts) < self.team_sync_ttl:
            err_entry = self._team_errors.get(tid)
            cached_err = err_entry[1] if (err_entry and (now - err_entry[0]) < 300) else None
            return (list(self._team_memory_records.get(tid, [])), cached_err)

        lock = self._get_lock(tid)
        async with lock:
            now = time.time()
            last_ts = self._team_sync_ts.get(tid, 0.0)
            if not force_refresh and (now - last_ts) < self.team_sync_ttl:
                err_entry = self._team_errors.get(tid)
                cached_err = err_entry[1] if (err_entry and (now - err_entry[0]) < 300) else None
                return (list(self._team_memory_records.get(tid, [])), cached_err)

            res = await self.client.fetch_team_history_with_fallback(
                tid,
                team_name=team_name,
                target_kickoff_utc=target_kickoff_utc,
            )
            if res["success"]:
                matches = res["matches"]
                self._team_memory_records[tid] = matches
                self._team_sync_ts[tid] = time.time()
                self._team_errors.pop(tid, None)
                if persist_to_db and db is not None and matches:
                    await self._persist_verified_matches_to_db(db, matches)
                return (matches, None)

            err_obj = {
                "error_code": res.get("error_code"),
                "error_message": res.get("error_message"),
            }
            self._trip_circuit_if_fatal(res.get("error_code"), res.get("error_message"))
            self._team_errors[tid] = (time.time(), err_obj)
            # Only throttle failed non-credit lookups briefly (60s)
            if res.get("error_code") not in ("INSUFFICIENT_CREDITS", "AUTH_FAILURE"):
                self._team_sync_ts[tid] = time.time() - self.team_sync_ttl + 60.0
            return (list(self._team_memory_records.get(tid, [])), err_obj)

    def _combine_pool(self, *pools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        for pool in pools:
            for item in pool:
                fid = str(item.get("fixture_id") or "").strip()
                if fid and fid in seen_ids:
                    continue
                if fid:
                    seen_ids.add(fid)
                merged.append(item)
        return merged

    async def get_team_form(
        self,
        db: Optional[AsyncSession],
        *,
        team_name: str,
        team_id: Optional[str] = None,
        venue: Optional[str] = None,
        league_id: str = "",
        country: Optional[str] = None,
        before_utc: Optional[datetime] = None,
        exclude_fixture_id: Optional[str] = None,
        allow_live_fetch: bool = True,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Compute verified General, Home, or Away form for one team with on-demand API sync if incomplete."""
        cutoff = parse_to_utc_aware(before_utc) or datetime.now(timezone.utc)
        db_records = await self._load_verified_db_matches(
            db,
            team_ids={str(team_id)} if team_id else None,
            team_names={team_name},
        )
        mem_records = self._team_memory_records.get(str(team_id or "").strip(), [])
        pool = self._combine_pool(mem_records, db_records)
        prov_err = None

        dataset = compute_independent_team_form(
            pool,
            team_name=team_name,
            team_id=team_id,
            league_id=league_id,
            venue=venue,
            target_kickoff_utc=cutoff,
            exclude_fixture_id=exclude_fixture_id,
            limit=limit,
        )

        if dataset["verified_count"] < limit and allow_live_fetch:
            synced_records, prov_err = await self.sync_team_history_if_needed(
                db,
                team_name=team_name,
                team_id=team_id,
                country=country,
                target_kickoff_utc=cutoff,
            )
            if synced_records:
                pool = self._combine_pool(synced_records, pool)
            dataset = compute_independent_team_form(
                pool,
                team_name=team_name,
                team_id=team_id,
                league_id=league_id,
                venue=venue,
                target_kickoff_utc=cutoff,
                exclude_fixture_id=exclude_fixture_id,
                limit=limit,
                provider_error=prov_err,
            )

        return dataset

    async def get_fixture_forms(
        self,
        db: Optional[AsyncSession],
        *,
        home_team: str,
        away_team: str,
        home_team_id: Optional[str] = None,
        away_team_id: Optional[str] = None,
        league_id: str = "",
        country: Optional[str] = None,
        target_kickoff_utc: Optional[datetime] = None,
        target_external_id: Optional[str] = None,
        allow_live_fetch: bool = True,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Compute all 4 independent form datasets for a single fixture cleanly."""
        cutoff = parse_to_utc_aware(target_kickoff_utc) or datetime.now(timezone.utc)
        t_ids = {str(x).strip() for x in (home_team_id, away_team_id) if str(x or "").strip()}
        t_names = {x for x in (home_team, away_team) if x}

        db_records = await self._load_verified_db_matches(db, team_ids=t_ids, team_names=t_names)
        pool = self._combine_pool(
            self._team_memory_records.get(str(home_team_id or "").strip(), []),
            self._team_memory_records.get(str(away_team_id or "").strip(), []),
            db_records,
        )

        h_gen = compute_independent_team_form(
            pool, team_name=home_team, team_id=home_team_id, league_id=league_id,
            venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id, limit=limit,
        )
        h_hm = compute_independent_team_form(
            pool, team_name=home_team, team_id=home_team_id, league_id=league_id,
            venue="home", target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id, limit=limit,
        )
        a_gen = compute_independent_team_form(
            pool, team_name=away_team, team_id=away_team_id, league_id=league_id,
            venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id, limit=limit,
        )
        a_aw = compute_independent_team_form(
            pool, team_name=away_team, team_id=away_team_id, league_id=league_id,
            venue="away", target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id, limit=limit,
        )

        h_err = None
        a_err = None
        if allow_live_fetch:
            tasks = []
            need_home = h_gen["verified_count"] < limit or h_hm["verified_count"] < limit
            need_away = a_gen["verified_count"] < limit or a_aw["verified_count"] < limit
            if need_home:
                tasks.append(("home", home_team, home_team_id))
            if need_away:
                tasks.append(("away", away_team, away_team_id))
            if tasks:
                results = await asyncio.gather(
                    *(
                        self.sync_team_history_if_needed(
                            None,
                            team_name=spec_name,
                            team_id=spec_id,
                            country=country,
                            target_kickoff_utc=cutoff,
                            persist_to_db=False,
                        )
                        for _, spec_name, spec_id in tasks
                    ),
                    return_exceptions=True,
                )
                to_persist: List[Dict[str, Any]] = []
                for (side, _, _), res in zip(tasks, results):
                    if isinstance(res, Exception):
                        err_val = {"error_code": "PROVIDER_ERROR", "error_message": str(res)}
                        if side == "home":
                            h_err = err_val
                        else:
                            a_err = err_val
                    else:
                        recs, err_val = res
                        if recs:
                            pool = self._combine_pool(recs, pool)
                            to_persist.extend(recs)
                        if side == "home":
                            h_err = err_val
                        else:
                            a_err = err_val
                if db is not None and to_persist:
                    await self._persist_verified_matches_to_db(db, to_persist)

                h_gen = compute_independent_team_form(
                    pool, team_name=home_team, team_id=home_team_id, league_id=league_id,
                    venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id,
                    limit=limit, provider_error=h_err,
                )
                h_hm = compute_independent_team_form(
                    pool, team_name=home_team, team_id=home_team_id, league_id=league_id,
                    venue="home", target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id,
                    limit=limit, provider_error=h_err,
                )
                a_gen = compute_independent_team_form(
                    pool, team_name=away_team, team_id=away_team_id, league_id=league_id,
                    venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id,
                    limit=limit, provider_error=a_err,
                )
                a_aw = compute_independent_team_form(
                    pool, team_name=away_team, team_id=away_team_id, league_id=league_id,
                    venue="away", target_kickoff_utc=cutoff, exclude_fixture_id=target_external_id,
                    limit=limit, provider_error=a_err,
                )

        return self._format_fixture_form_bundle(h_gen, h_hm, a_gen, a_aw)

    @staticmethod
    def _format_fixture_form_bundle(
        h_gen: Dict[str, Any],
        h_hm: Dict[str, Any],
        a_gen: Dict[str, Any],
        a_aw: Dict[str, Any],
    ) -> Dict[str, Any]:
        err_code = h_gen.get("error_code") or h_hm.get("error_code") or a_gen.get("error_code") or a_aw.get("error_code")
        err_msg = h_gen.get("error_message") or h_hm.get("error_message") or a_gen.get("error_message") or a_aw.get("error_message")
        return {
            "home_form": h_gen["symbols"],
            "home_venue_form": h_hm["symbols"],
            "away_form": a_gen["symbols"],
            "away_venue_form": a_aw["symbols"],
            "home_form_details": h_gen["matches"],
            "home_venue_form_details": h_hm["matches"],
            "away_form_details": a_gen["matches"],
            "away_venue_form_details": a_aw["matches"],
            "home_overall": h_gen,
            "home_home": h_hm,
            "away_overall": a_gen,
            "away_away": a_aw,
            "form_completeness": {
                "home_overall": {"verified": h_gen["verified_count"], "requested": h_gen["requested_count"], "status": h_gen["status"], "label": h_gen["label"]},
                "home_home": {"verified": h_hm["verified_count"], "requested": h_hm["requested_count"], "status": h_hm["status"], "label": h_hm["label"]},
                "away_overall": {"verified": a_gen["verified_count"], "requested": a_gen["requested_count"], "status": a_gen["status"], "label": a_gen["label"]},
                "away_away": {"verified": a_aw["verified_count"], "requested": a_aw["requested_count"], "status": a_aw["status"], "label": a_aw["label"]},
            },
            "form_provider": AUTHORITATIVE_PROVIDER,
            "form_error_code": err_code,
            "form_error_message": err_msg,
        }

    async def hydrate_fixtures_batch(
        self,
        db: Optional[AsyncSession],
        fixtures: Sequence[Any],
        *,
        sync_candidate_slice: Optional[Sequence[Any]] = None,
        allow_live_fetch: bool = True,
        limit: int = 5,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch-compute independent pre-kickoff forms for a list of fixtures.
        Fetches Live Football API `/team_matches` concurrently ONLY for teams in `sync_candidate_slice`
        (defaults to all fixtures up to 24 fixtures = 48 teams) that do not already have full 5/5+5/5 coverage.
        Returns mapping keyed by normalized fixture key `(external_id or index)`.
        """
        if not fixtures:
            return {}

        t_ids: Set[str] = set()
        t_names: Set[str] = set()
        for fx in fixtures:
            for attr in ("home_team_id", "away_team_id"):
                val = getattr(fx, attr, None) if not isinstance(fx, dict) else fx.get(attr)
                if val:
                    t_ids.add(str(val).strip())
            for attr in ("home_team", "away_team"):
                val = getattr(fx, attr, None) if not isinstance(fx, dict) else fx.get(attr)
                if val:
                    t_names.add(str(val).strip())

        db_records = await self._load_verified_db_matches(db, team_ids=t_ids, team_names=t_names)
        extra_mem: List[Dict[str, Any]] = []
        for tid in t_ids:
            if tid in self._team_memory_records:
                extra_mem.extend(self._team_memory_records[tid])
        pool = self._combine_pool(extra_mem, db_records)

        # First pass purely from verified Live Football API DB/memory pool
        def _extract_fields(fx: Any, idx: int) -> Dict[str, Any]:
            is_d = isinstance(fx, dict)
            fid = str((fx.get("external_id") if is_d else getattr(fx, "external_id", None)) or f"idx_{idx}").strip()
            ht = str((fx.get("home_team") if is_d else getattr(fx, "home_team", "")) or "").strip()
            at = str((fx.get("away_team") if is_d else getattr(fx, "away_team", "")) or "").strip()
            hid = str((fx.get("home_team_id") if is_d else getattr(fx, "home_team_id", None)) or "").strip() or None
            aid = str((fx.get("away_team_id") if is_d else getattr(fx, "away_team_id", None)) or "").strip() or None
            lg = str((fx.get("league_id") if is_d else getattr(fx, "league_id", "")) or "").strip()
            country = str((fx.get("country") if is_d else getattr(fx, "country", "")) or "").strip()
            ko = parse_to_utc_aware(fx.get("match_date") if is_d else getattr(fx, "match_date", None))
            return {"key": fid, "ht": ht, "at": at, "hid": hid, "aid": aid, "lg": lg, "country": country, "ko": ko}

        meta_list = [_extract_fields(fx, i) for i, fx in enumerate(fixtures)]
        sync_keys = {
            _extract_fields(fx, i)["key"]
            for i, fx in enumerate(sync_candidate_slice if sync_candidate_slice is not None else fixtures[:24])
        }

        missing_teams: Dict[str, Dict[str, Any]] = {}
        for info in meta_list:
            if info["key"] not in sync_keys:
                continue
            cutoff = info["ko"] or datetime.now(timezone.utc)
            h_gen = compute_independent_team_form(
                pool, team_name=info["ht"], team_id=info["hid"], league_id=info["lg"],
                venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=info["key"], limit=limit,
            )
            h_hm = compute_independent_team_form(
                pool, team_name=info["ht"], team_id=info["hid"], league_id=info["lg"],
                venue="home", target_kickoff_utc=cutoff, exclude_fixture_id=info["key"], limit=limit,
            )
            a_gen = compute_independent_team_form(
                pool, team_name=info["at"], team_id=info["aid"], league_id=info["lg"],
                venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=info["key"], limit=limit,
            )
            a_aw = compute_independent_team_form(
                pool, team_name=info["at"], team_id=info["aid"], league_id=info["lg"],
                venue="away", target_kickoff_utc=cutoff, exclude_fixture_id=info["key"], limit=limit,
            )
            if (h_gen["verified_count"] < limit or h_hm["verified_count"] < limit) and (info["hid"] or info["ht"]):
                mk = info["hid"] or canon_team_keys(info["ht"])[0]
                if mk not in missing_teams:
                    missing_teams[mk] = {
                        "team_name": info["ht"],
                        "team_id": info["hid"],
                        "country": info["country"],
                        "target_kickoff_utc": cutoff,
                    }
            if (a_gen["verified_count"] < limit or a_aw["verified_count"] < limit) and (info["aid"] or info["at"]):
                mk = info["aid"] or canon_team_keys(info["at"])[0]
                if mk not in missing_teams:
                    missing_teams[mk] = {
                        "team_name": info["at"],
                        "team_id": info["aid"],
                        "country": info["country"],
                        "target_kickoff_utc": cutoff,
                    }

        team_err_by_key: Dict[str, Optional[Dict[str, Optional[str]]]] = {}
        if allow_live_fetch and missing_teams and self.client.is_configured():
            sem = asyncio.Semaphore(5)

            async def _fetch_one(mk: str, spec: Dict[str, Any]):
                async with sem:
                    if self._is_circuit_open():
                        return mk, [], self._is_circuit_open()
                    recs, err = await self.sync_team_history_if_needed(
                        None,
                        team_name=spec["team_name"],
                        team_id=spec["team_id"],
                        country=spec.get("country"),
                        target_kickoff_utc=spec.get("target_kickoff_utc"),
                        persist_to_db=False,
                    )
                    return mk, recs, err

            batch_res = await asyncio.gather(*(_fetch_one(mk, spec) for mk, spec in missing_teams.items()))
            fetched_pools: List[List[Dict[str, Any]]] = []
            flat_to_persist: List[Dict[str, Any]] = []
            for mk, recs, err in batch_res:
                if err:
                    team_err_by_key[mk] = err
                if recs:
                    fetched_pools.append(recs)
                    flat_to_persist.extend(recs)
            if db is not None and flat_to_persist:
                await self._persist_verified_matches_to_db(db, flat_to_persist)
            if fetched_pools:
                pool = self._combine_pool(pool, *fetched_pools)

        # Final deterministic assembly for every fixture
        out: Dict[str, Dict[str, Any]] = {}
        for info in meta_list:
            cutoff = info["ko"] or datetime.now(timezone.utc)
            h_mk = info["hid"] or canon_team_keys(info["ht"])[0]
            a_mk = info["aid"] or canon_team_keys(info["at"])[0]
            h_err = team_err_by_key.get(h_mk) or self._is_circuit_open()
            a_err = team_err_by_key.get(a_mk) or self._is_circuit_open()

            h_gen = compute_independent_team_form(
                pool, team_name=info["ht"], team_id=info["hid"], league_id=info["lg"],
                venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=info["key"],
                limit=limit, provider_error=h_err,
            )
            h_hm = compute_independent_team_form(
                pool, team_name=info["ht"], team_id=info["hid"], league_id=info["lg"],
                venue="home", target_kickoff_utc=cutoff, exclude_fixture_id=info["key"],
                limit=limit, provider_error=h_err,
            )
            a_gen = compute_independent_team_form(
                pool, team_name=info["at"], team_id=info["aid"], league_id=info["lg"],
                venue=None, target_kickoff_utc=cutoff, exclude_fixture_id=info["key"],
                limit=limit, provider_error=a_err,
            )
            a_aw = compute_independent_team_form(
                pool, team_name=info["at"], team_id=info["aid"], league_id=info["lg"],
                venue="away", target_kickoff_utc=cutoff, exclude_fixture_id=info["key"],
                limit=limit, provider_error=a_err,
            )
            out[info["key"]] = self._format_fixture_form_bundle(h_gen, h_hm, a_gen, a_aw)

        return out

    async def get_head_to_head(
        self,
        db: Optional[AsyncSession],
        *,
        home_team: str,
        away_team: str,
        home_team_id: Optional[str] = None,
        away_team_id: Optional[str] = None,
        target_kickoff_utc: Optional[datetime] = None,
        target_external_id: Optional[str] = None,
        allow_live_fetch: bool = True,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Compute verified pre-kickoff H2H strictly from Live Football API history + optional `/h2h`."""
        cutoff = parse_to_utc_aware(target_kickoff_utc) or datetime.now(timezone.utc)
        t_ids = {str(x).strip() for x in (home_team_id, away_team_id) if str(x or "").strip()}
        db_records = await self._load_verified_db_matches(
            db, team_ids=t_ids or None, team_names={home_team, away_team}
        )
        pool = self._combine_pool(
            self._team_memory_records.get(str(home_team_id or "").strip(), []),
            self._team_memory_records.get(str(away_team_id or "").strip(), []),
            db_records,
        )

        extra_h2h_rows: List[Dict[str, Any]] = []
        mid = str(target_external_id or "").strip()
        if allow_live_fetch and mid.isdigit() and self.client.is_configured():
            cached = self._h2h_cache.get(mid)
            if cached and (time.time() - cached[0]) < 1800:
                h2h_payload = cached[1]
            else:
                res = await self.client.fetch_match_h2h(mid)
                h2h_payload = res.get("data") if res.get("success") and isinstance(res.get("data"), dict) else {}
                if h2h_payload:
                    self._h2h_cache[mid] = (time.time(), h2h_payload)
            if h2h_payload and isinstance(h2h_payload.get("h2h"), list):
                extra_h2h_rows = h2h_payload["h2h"]

        return compute_independent_h2h(
            pool,
            home_team=home_team,
            away_team=away_team,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            target_kickoff_utc=cutoff,
            exclude_fixture_id=target_external_id,
            limit=limit,
            extra_h2h_rows=extra_h2h_rows,
        )


# Shared application singleton initialized lazily
_shared_integrity_engine: Optional[TeamFormIntegrityEngine] = None


def get_team_form_integrity_engine() -> TeamFormIntegrityEngine:
    global _shared_integrity_engine
    if _shared_integrity_engine is None:
        _shared_integrity_engine = TeamFormIntegrityEngine()
    return _shared_integrity_engine
