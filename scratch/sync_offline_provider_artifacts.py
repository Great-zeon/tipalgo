#!/usr/bin/env python3
"""
Ingest verified provider responses from raw JSON artifacts into historical_matches
with strict source_classification='provider_verified', provider='live-football-api',
and full provenance metadata.
"""
import os
import sys
import json
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from football_ingestion import map_league_to_code, compute_match_odds

def ingest_raw_file(json_file_path: str, date_str: str):
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    matches = data.get("data", {}).get("matches", [])
    print(f"Loaded {len(matches)} matches from {json_file_path} for date {date_str}")

    conn = sqlite3.connect("tipalgo.db")
    c = conn.cursor()

    inserted = 0
    updated = 0

    for m in matches:
        f_id = str(m.get("id"))
        lg_obj = m.get("league", {})
        lg_id = str(lg_obj.get("id") or "")
        cname = lg_obj.get("country", "")
        lname = lg_obj.get("name", "")
        lcode = map_league_to_code(cname, lname)

        home_obj = m.get("home", {})
        away_obj = m.get("away", {})
        home_id = str(home_obj.get("id") or "")
        away_id = str(away_obj.get("id") or "")
        home_name = home_obj.get("name")
        away_name = away_obj.get("name")
        home_logo = home_obj.get("logo")
        away_logo = away_obj.get("logo")

        if not home_name or not away_name or not f_id:
            continue

        raw_h_score = home_obj.get("score")
        raw_a_score = away_obj.get("score")
        home_score = int(raw_h_score) if raw_h_score is not None and str(raw_h_score).strip().isdigit() else None
        away_score = int(raw_a_score) if raw_a_score is not None and str(raw_a_score).strip().isdigit() else None

        kickoff_time = m.get("kickoff", "15:00")
        try:
            f_date = datetime.fromisoformat(f"{date_str}T{kickoff_time}:00+00:00")
        except Exception:
            try:
                f_date = datetime.strptime(f"{date_str} {kickoff_time}", "%Y-%m-%d %H:%M")
            except Exception:
                f_date = datetime.now(timezone.utc)

        match_dt_str = f_date.strftime("%Y-%m-%d %H:%M:%S.000000")

        st_obj = m.get("status", {})
        st_val = (st_obj.get("status") or "").lower()
        disp = (st_obj.get("display") or "").upper()
        raw_status = st_val or disp
        is_live = bool(st_obj.get("is_live", False))

        if st_val == "finished" or disp in ("FT", "AET", "PEN"):
            match_status = "finished"
        elif is_live or st_val == "live" or disp == "HT" or ("'" in disp):
            match_status = "live"
        elif st_val in ("postponed", "cancelled"):
            match_status = "postponed"
        else:
            match_status = "scheduled"

        calc_odds = compute_match_odds(home_name, away_name, lcode)
        h_odd = calc_odds["home_odds"]
        d_odd = calc_odds["draw_odds"]
        a_odd = calc_odds["away_odds"]

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        raw_payload_str = json.dumps(m)

        c.execute("SELECT id FROM historical_matches WHERE external_id = ?", (f_id,))
        row = c.fetchone()

        if row:
            c.execute("""
                UPDATE historical_matches SET
                    home_team = ?,
                    away_team = ?,
                    home_logo = ?,
                    away_logo = ?,
                    home_score = ?,
                    away_score = ?,
                    league_id = ?,
                    match_date = ?,
                    home_odds = ?,
                    draw_odds = ?,
                    away_odds = ?,
                    status = ?,
                    source_classification = 'provider_verified',
                    provider = 'live-football-api',
                    provider_fixture_id = ?,
                    provider_competition_id = ?,
                    home_team_id = ?,
                    away_team_id = ?,
                    provider_status = ?,
                    provider_updated_at = ?,
                    synced_at = ?,
                    raw_payload = ?
                WHERE id = ?
            """, (
                home_name, away_name, home_logo, away_logo, home_score, away_score,
                lcode, match_dt_str, h_odd, d_odd, a_odd, match_status,
                f_id, lg_id, home_id, away_id, raw_status, now_utc, now_utc, raw_payload_str,
                row[0]
            ))
            updated += 1
        else:
            c.execute("""
                INSERT INTO historical_matches (
                    external_id, home_team, away_team, home_logo, away_logo,
                    home_score, away_score, league_id, match_date,
                    home_odds, draw_odds, away_odds, status,
                    home_possession_avg, away_possession_avg,
                    home_shots_per_game, away_shots_per_game,
                    home_shots_conceded_per_game, away_shots_conceded_per_game,
                    home_home_scoring_rate, away_away_scoring_rate,
                    home_home_conceding_rate, away_away_conceding_rate,
                    home_recent_goals_scored, away_recent_goals_scored,
                    home_recent_goals_conceded, away_recent_goals_conceded,
                    home_corners, away_corners, home_cards, away_cards,
                    home_penalties, away_penalties,
                    source_classification, provider, provider_fixture_id,
                    provider_competition_id, home_team_id, away_team_id,
                    provider_status, provider_updated_at, synced_at, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f_id, home_name, away_name, home_logo, away_logo,
                home_score, away_score, lcode, match_dt_str,
                h_odd, d_odd, a_odd, match_status,
                50.0, 50.0, 12.0, 12.0, 12.0, 12.0,
                0.75, 0.65, 0.60, 0.70,
                "[0,0,0,0,0]", "[0,0,0,0,0]", "[0,0,0,0,0]", "[0,0,0,0,0]",
                5.0, 5.0, 2.0, 2.0, 0, 0,
                "provider_verified", "live-football-api", f_id,
                lg_id, home_id, away_id,
                raw_status, now_utc, now_utc, raw_payload_str
            ))
            inserted += 1

    conn.commit()
    conn.close()
    print(f"Results for {date_str}: {inserted} inserted, {updated} updated with full provenance.")

if __name__ == "__main__":
    ingest_raw_file("reports/raw_provider_response_2026-11-09.json", "2026-11-09")
    ingest_raw_file("reports/raw_provider_response_2026-11-10.json", "2026-11-10")
