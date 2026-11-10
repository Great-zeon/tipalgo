#!/usr/bin/env python3
"""
Seed realistic, authentic upcoming calendar fixtures for:
- Monday, Nov 09, 2026 (Today)
- Tuesday, Nov 10, 2026
- Wednesday, Nov 11, 2026
- Thursday, Nov 12, 2026
Across top leagues in SportyMill so the calendar always has rich, active slates
regardless of external API connection or offline status.
"""
import os
import sys
import sqlite3
import hashlib
from datetime import datetime, timezone

def seed_calendar_slates():
    # Production Data Integrity Safeguard:
    # Synthetic fixture seeding is prohibited in production environments or live slates.
    if os.getenv("ENVIRONMENT") == "production" or os.getenv("TIPALGO_ENV") == "production":
        print("Data Integrity Protection: Synthetic fixture seeding is disabled in production environments.")
        return

    conn = sqlite3.connect("tipalgo.db")
    c = conn.cursor()

    slates = [
        # Monday, Nov 09, 2026
        ("2026-11-09", [
            # English Premier League (Monday Night Football)
            ("PL", "Nottingham Forest", "Newcastle", "20:00:00", 3.20, 3.40, 2.25, 5.2, 5.8, 1.35, 1.65),
            ("PL", "Fulham", "Brentford", "20:00:00", 2.10, 3.50, 3.40, 5.5, 4.8, 1.60, 1.25),
            # Spanish La Liga
            ("PD", "Osasuna", "Valencia", "21:00:00", 2.20, 3.20, 3.50, 4.8, 4.2, 1.45, 1.15),
            ("PD", "Espanyol", "Málaga", "19:00:00", 1.95, 3.30, 4.20, 5.2, 4.0, 1.70, 0.95),
            # Italian Serie A
            ("SA", "Bologna", "Lazio", "20:45:00", 2.45, 3.25, 2.95, 5.0, 4.8, 1.40, 1.30),
            ("SA", "Torino", "Monza", "18:30:00", 1.85, 3.40, 4.50, 5.6, 3.8, 1.65, 0.90),
            # Dutch Eredivisie
            ("DED", "Twente", "Utrecht", "20:00:00", 1.90, 3.60, 3.90, 6.2, 4.5, 1.85, 1.20),
            ("DED", "Go Ahead Eagles", "Heracles", "18:45:00", 2.15, 3.50, 3.25, 5.4, 4.8, 1.55, 1.35),
            # Colombia Primera A
            ("COL", "Alianza Valledupar", "América", "22:15:00", 2.80, 3.00, 2.65, 4.5, 4.8, 1.25, 1.35),
            ("COL", "Llaneros", "Santa Fe", "20:00:00", 3.10, 3.10, 2.35, 4.2, 5.0, 1.15, 1.45),
            # Argentina Liga Profesional
            ("ARG", "Instituto", "Lanús", "23:00:00", 2.50, 3.00, 3.10, 4.6, 4.2, 1.30, 1.10),
            ("ARG", "Vélez", "Huracán", "21:00:00", 1.95, 3.15, 4.20, 5.4, 3.8, 1.55, 0.95),
            # English Championship
            ("ELC", "Leeds United", "Hull City", "20:00:00", 1.60, 4.00, 5.50, 6.8, 3.8, 2.10, 0.90),
            ("ELC", "Middlesbrough", "Sunderland", "19:45:00", 2.25, 3.35, 3.15, 5.5, 5.0, 1.50, 1.30),
            # French Ligue 1
            ("FL1", "Lille", "Rennes", "20:45:00", 1.95, 3.50, 3.80, 5.8, 4.6, 1.75, 1.20),
        ]),

        # Tuesday, Nov 10, 2026 (European & League Slate)
        ("2026-11-10", [
            ("PL", "Chelsea", "Tottenham", "20:00:00", 2.15, 3.65, 3.20, 6.0, 5.5, 1.80, 1.50),
            ("PD", "Athletic Club", "Real Madrid", "21:00:00", 3.80, 3.60, 1.95, 5.2, 6.4, 1.25, 1.90),
            ("SA", "Juventus", "Atalanta", "20:45:00", 2.10, 3.40, 3.50, 5.5, 5.2, 1.60, 1.20),
            ("BL1", "Dortmund", "Stuttgart", "20:30:00", 1.85, 3.90, 3.80, 6.8, 5.0, 2.20, 1.45),
            ("DED", "Ajax", "Feyenoord", "20:00:00", 2.30, 3.60, 2.90, 6.4, 5.8, 1.85, 1.65),
            ("COL", "Millonarios", "Atlético Nacional", "21:30:00", 2.20, 3.10, 3.40, 5.0, 4.5, 1.40, 1.15),
            ("ARG", "Boca Juniors", "Racing Club", "22:00:00", 2.05, 3.15, 3.80, 5.2, 4.2, 1.50, 1.05),
            ("ELC", "Coventry", "West Brom", "19:45:00", 2.40, 3.30, 3.00, 5.2, 4.8, 1.45, 1.30),
        ]),

        # Wednesday, Nov 11, 2026 (Midweek Action)
        ("2026-11-11", [
            ("PL", "Arsenal", "Liverpool", "20:00:00", 2.35, 3.50, 2.95, 6.5, 6.0, 1.90, 1.65),
            ("PL", "Manchester City", "Aston Villa", "20:00:00", 1.45, 4.75, 6.50, 7.5, 4.0, 2.45, 0.95),
            ("PD", "Barcelona", "Atlético Madrid", "21:00:00", 1.85, 3.75, 4.00, 6.5, 4.5, 2.10, 1.20),
            ("SA", "Inter", "Milan", "20:45:00", 2.05, 3.45, 3.60, 5.8, 5.2, 1.70, 1.30),
            ("BL1", "Bayern Munich", "Leipzig", "20:30:00", 1.55, 4.50, 5.50, 7.2, 4.6, 2.50, 1.25),
            ("FL1", "Monaco", "Marseille", "21:00:00", 2.25, 3.50, 3.10, 5.6, 5.4, 1.65, 1.40),
            ("COL", "Junior", "Deportes Tolima", "21:00:00", 2.10, 3.15, 3.60, 4.8, 4.2, 1.35, 1.10),
            ("ARG", "River Plate", "Independiente", "22:30:00", 1.70, 3.50, 5.20, 6.0, 3.8, 1.90, 0.85),
        ]),

        # Thursday, Nov 12, 2026
        ("2026-11-12", [
            ("ELC", "Blackburn", "Preston", "19:45:00", 2.20, 3.30, 3.30, 5.0, 4.8, 1.40, 1.25),
            ("SA", "Napoli", "Fiorentina", "20:45:00", 1.75, 3.70, 4.60, 6.2, 4.4, 1.95, 1.05),
            ("PD", "Real Sociedad", "Real Betis", "21:00:00", 2.15, 3.30, 3.45, 5.4, 4.6, 1.50, 1.20),
            ("DED", "PSV Eindhoven", "AZ Alkmaar", "20:00:00", 1.55, 4.25, 5.50, 7.0, 4.5, 2.35, 1.15),
            ("COL", "Independiente Medellín", "Once Caldas", "21:30:00", 2.00, 3.20, 3.80, 5.0, 4.0, 1.45, 1.05),
        ])
    ]

    total_inserted = 0

    for date_str, matches in slates:
        for m in matches:
            lid, ht, at, kickoff, ho, do, ao, hc, ac, h_xg, a_xg = m
            ext_id = f"slate_{lid}_{ht}_{at}_{date_str.replace('-','')}"
            match_dt = f"{date_str} {kickoff}.000000"

            c.execute("SELECT id FROM historical_matches WHERE external_id = ?", (ext_id,))
            exists = c.fetchone()
            if not exists:
                c.execute("""
                    INSERT INTO historical_matches (
                        external_id, home_team, away_team, home_score, away_score,
                        league_id, match_date, home_odds, away_odds, draw_odds,
                        status, home_possession_avg, away_possession_avg,
                        home_shots_per_game, away_shots_per_game,
                        home_shots_conceded_per_game, away_shots_conceded_per_game,
                        home_home_scoring_rate, away_away_scoring_rate,
                        home_home_conceding_rate, away_away_conceding_rate,
                        home_recent_goals_scored, away_recent_goals_scored,
                        home_recent_goals_conceded, away_recent_goals_conceded,
                        home_corners, away_corners, home_cards, away_cards,
                        home_penalties, away_penalties, source_classification, provider
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ext_id, ht, at, None, None,
                    lid, match_dt, ho, ao, do,
                    "scheduled", 52.5, 47.5,
                    14.2, 11.5,
                    10.5, 13.8,
                    h_xg, a_xg,
                    1.0, 1.4,
                    "2,1,1", "1,0,2",
                    "1,0,1", "2,1,1",
                    hc, ac, 1.8, 2.2,
                    0, 0, "synthetic", "seed_script"
                ))
                total_inserted += 1

    conn.commit()
    conn.close()
    print(f"Upcoming fixtures seeded successfully: Inserted {total_inserted} matches across Nov 09-12.")

if __name__ == "__main__":
    seed_calendar_slates()

