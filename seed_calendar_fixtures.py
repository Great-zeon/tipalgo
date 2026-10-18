#!/usr/bin/env python3
import asyncio
from datetime import datetime
from sqlalchemy import select
from main import AsyncSessionLocal, LeagueTable, HistoricalMatch

async def seed_calendar_data():
    async with AsyncSessionLocal() as db:
        # 1. UNL teams
        unl_teams = [
            ("Portugal", 1, 6, 5, 1, 0, 15, 4, 11, 16, "WWWDW", 3, 3, 0, 0, 9, 2, 3, 2, 1, 0, 6, 2, 0.50, 0.33, 0.0, 0.0, 6.5, 5.2, 1.8, 1.9, 2.30, 0.60, 2.10, 0.70),
            ("Norway", 2, 6, 3, 2, 1, 12, 7, 5, 11, "WDWLW", 3, 2, 1, 0, 8, 3, 3, 1, 1, 1, 4, 4, 0.33, 0.25, 0.15, 0.20, 5.8, 4.5, 2.1, 2.2, 1.80, 1.10, 1.45, 1.25),
            ("Denmark", 1, 6, 4, 1, 1, 11, 5, 6, 13, "WWDLW", 3, 2, 1, 0, 6, 2, 3, 2, 0, 1, 5, 3, 0.50, 0.33, 0.10, 0.20, 5.5, 4.8, 1.9, 2.0, 1.75, 0.85, 1.50, 1.00),
            ("Wales", 2, 6, 2, 3, 1, 8, 7, 1, 9, "WDWLD", 3, 1, 2, 0, 4, 3, 3, 1, 1, 1, 4, 4, 0.33, 0.20, 0.20, 0.25, 4.8, 4.2, 2.3, 2.4, 1.35, 1.20, 1.25, 1.30),
            ("Austria", 1, 6, 4, 1, 1, 14, 6, 8, 13, "WWDLW", 3, 3, 0, 0, 9, 2, 3, 1, 1, 1, 5, 4, 0.50, 0.25, 0.10, 0.15, 6.0, 5.0, 2.0, 2.1, 1.95, 0.95, 1.60, 1.15),
            ("Kosovo", 3, 6, 2, 1, 3, 7, 10, -3, 7, "WLLDW", 3, 1, 1, 1, 4, 4, 3, 1, 0, 2, 3, 6, 0.20, 0.15, 0.30, 0.35, 4.5, 3.8, 2.5, 2.6, 1.20, 1.50, 1.05, 1.80),
            ("Azerbaijan", 3, 6, 1, 2, 3, 5, 9, -4, 5, "DLDLL", 3, 1, 1, 1, 3, 4, 3, 0, 1, 2, 2, 5, 0.25, 0.10, 0.35, 0.40, 4.2, 3.5, 2.2, 2.4, 1.05, 1.55, 0.90, 1.70),
            ("Lithuania", 4, 6, 1, 1, 4, 4, 10, -6, 4, "LLWDL", 3, 1, 0, 2, 2, 4, 3, 0, 1, 2, 2, 6, 0.15, 0.10, 0.40, 0.45, 4.0, 3.2, 2.4, 2.5, 0.95, 1.65, 0.85, 1.80),
            ("Malta", 3, 6, 1, 1, 4, 3, 9, -6, 4, "DLLDL", 3, 1, 1, 1, 2, 3, 3, 0, 0, 3, 1, 6, 0.20, 0.10, 0.45, 0.50, 3.8, 3.0, 2.3, 2.5, 0.85, 1.60, 0.70, 1.85),
            ("Andorra", 4, 6, 0, 2, 4, 2, 8, -6, 2, "DDLLL", 3, 0, 2, 1, 1, 3, 3, 0, 0, 3, 1, 5, 0.25, 0.05, 0.50, 0.55, 3.5, 2.8, 2.6, 2.8, 0.75, 1.50, 0.65, 1.75),
        ]

        for t in unl_teams:
            res = await db.execute(select(LeagueTable).where(LeagueTable.league_id == "UNL", LeagueTable.team_name == t[0]))
            if not res.scalar_one_or_none():
                db.add(LeagueTable(
                    league_id="UNL", team_name=t[0], position=t[1], played=t[2], wins=t[3], draws=t[4], losses=t[5],
                    goals_for=t[6], goals_against=t[7], goal_difference=t[8], points=t[9], last_5_form=t[10],
                    home_played=t[11], home_wins=t[12], home_draws=t[13], home_losses=t[14], home_goals_for=t[15], home_goals_against=t[16],
                    away_played=t[17], away_wins=t[18], away_draws=t[19], away_losses=t[20], away_goals_for=t[21], away_goals_against=t[22],
                    home_clean_sheet_pct=t[23], away_clean_sheet_pct=t[24], home_failed_to_score_pct=t[25], away_failed_to_score_pct=t[26],
                    home_corners_avg=t[27], away_corners_avg=t[28], home_cards_avg=t[29], away_cards_avg=t[30],
                    xg_for_home=t[31], xg_against_home=t[32], xg_for_away=t[33], xg_against_away=t[34]
                ))

        # 2. Oct 4 matches from screenshot
        oct4_matches = [
            ("UNL-20261004-01", "Azerbaijan", "Lithuania", "UNL", datetime.fromisoformat("2026-10-04 14:00:00"), 2.15, 3.10, 3.60),
            ("UNL-20261004-02", "Malta", "Andorra", "UNL", datetime.fromisoformat("2026-10-04 17:00:00"), 2.05, 3.00, 4.10),
            ("UNL-20261004-03", "Kosovo", "Austria", "UNL", datetime.fromisoformat("2026-10-04 17:00:00"), 4.30, 3.60, 1.80),
            ("UNL-20261004-04", "Portugal", "Norway", "UNL", datetime.fromisoformat("2026-10-04 19:45:00"), 1.62, 4.10, 5.20),
            ("UNL-20261004-05", "Wales", "Denmark", "UNL", datetime.fromisoformat("2026-10-04 19:45:00"), 3.20, 3.25, 2.30),
        ]

        for m in oct4_matches:
            res = await db.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == m[0]))
            if not res.scalar_one_or_none():
                db.add(HistoricalMatch(
                    external_id=m[0], home_team=m[1], away_team=m[2], league_id=m[3],
                    match_date=m[4], home_odds=m[5], draw_odds=m[6], away_odds=m[7],
                    status="scheduled"
                ))

        # 3. Forward matches Oct 9-14
        forward_matches = [
            ("FUT-20261009-01", "Liverpool", "Manchester United", "PL", datetime.fromisoformat("2026-10-09 20:00:00"), 1.55, 4.30, 5.50),
            ("FUT-20261009-02", "Gremio", "Cruzeiro", "BSA", datetime.fromisoformat("2026-10-09 23:00:00"), 2.10, 3.10, 3.70),
            ("FUT-20261010-01", "Arsenal", "Manchester City", "PL", datetime.fromisoformat("2026-10-10 12:30:00"), 2.35, 3.40, 2.95),
            ("FUT-20261010-02", "Chelsea", "Tottenham", "PL", datetime.fromisoformat("2026-10-10 15:00:00"), 2.10, 3.60, 3.30),
            ("FUT-20261010-03", "Aston Villa", "Newcastle", "PL", datetime.fromisoformat("2026-10-10 17:30:00"), 2.20, 3.50, 3.15),
            ("FUT-20261010-04", "Real Madrid", "Atletico Madrid", "PD", datetime.fromisoformat("2026-10-10 20:00:00"), 1.85, 3.65, 4.00),
            ("FUT-20261011-01", "Barcelona", "Athletic Club", "PD", datetime.fromisoformat("2026-10-11 16:15:00"), 1.65, 4.00, 5.00),
            ("FUT-20261011-02", "West Ham", "Brighton", "PL", datetime.fromisoformat("2026-10-11 14:00:00"), 2.45, 3.40, 2.85),
            ("FUT-20261011-03", "Flamengo", "Palmeiras", "BSA", datetime.fromisoformat("2026-10-11 20:00:00"), 2.25, 3.20, 3.30),
            ("FUT-20261012-01", "Boca Juniors", "River Plate", "ARG", datetime.fromisoformat("2026-10-12 21:00:00"), 2.40, 3.00, 3.20),
            ("FUT-20261012-02", "Racing Club", "San Lorenzo", "ARG", datetime.fromisoformat("2026-10-12 23:15:00"), 2.05, 3.05, 3.90),
            ("FUT-20261013-01", "Portugal", "Denmark", "UNL", datetime.fromisoformat("2026-10-13 19:45:00"), 1.70, 3.80, 4.80),
            ("FUT-20261013-02", "Norway", "Austria", "UNL", datetime.fromisoformat("2026-10-13 19:45:00"), 2.25, 3.30, 3.20),
            ("FUT-20261014-01", "Lithuania", "Malta", "UNL", datetime.fromisoformat("2026-10-14 17:00:00"), 2.20, 3.10, 3.50),
            ("FUT-20261014-02", "Andorra", "Kosovo", "UNL", datetime.fromisoformat("2026-10-14 19:45:00"), 4.80, 3.50, 1.75),
        ]

        for m in forward_matches:
            res = await db.execute(select(HistoricalMatch).where(HistoricalMatch.external_id == m[0]))
            if not res.scalar_one_or_none():
                db.add(HistoricalMatch(
                    external_id=m[0], home_team=m[1], away_team=m[2], league_id=m[3],
                    match_date=m[4], home_odds=m[5], draw_odds=m[6], away_odds=m[7],
                    status="scheduled"
                ))

        await db.commit()
    print("Database updated with UNL teams, Oct 4 fixtures, and forward schedule!")

if __name__ == "__main__":
    asyncio.run(seed_calendar_data())
