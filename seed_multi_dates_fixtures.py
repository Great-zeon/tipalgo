import sqlite3

conn = sqlite3.connect('tipalgo.db')
c = conn.cursor()

fixtures_to_add = [
    # 2026-10-14: Wednesday Champions League Night
    ("CL-20261014-01", "Real Madrid", "Marseille", "CL", "2026-10-14 20:00:00", 1.28, 5.80, 9.50, "scheduled"),
    ("CL-20261014-02", "Bayern Munich", "Arsenal", "CL", "2026-10-14 20:00:00", 1.85, 3.80, 3.90, "scheduled"),
    ("CL-20261014-03", "Inter", "Monaco", "CL", "2026-10-14 20:00:00", 1.55, 4.20, 5.80, "scheduled"),
    ("CL-20261014-04", "Barcelona", "Sporting CP", "CL", "2026-10-14 20:00:00", 1.35, 5.20, 7.80, "scheduled"),
    ("CL-20261014-05", "Paris Saint Germain", "Bayer Leverkusen", "CL", "2026-10-14 20:00:00", 1.70, 4.00, 4.60, "scheduled"),
    ("CL-20261014-06", "Manchester City", "Atalanta", "CL", "2026-10-14 20:00:00", 1.25, 6.20, 11.00, "scheduled"),
    ("CL-20261014-07", "Liverpool", "Juventus", "CL", "2026-10-14 20:00:00", 1.62, 4.10, 5.20, "scheduled"),
    ("CL-20261014-08", "Chelsea", "Benfica", "CL", "2026-10-14 20:00:00", 1.58, 4.10, 5.50, "scheduled"),

    # 2026-10-18: Sunday European Super Matchday
    # Premier League
    ("PL-20261018-01", "Liverpool", "Manchester United", "PL", "2026-10-18 16:30:00", 1.52, 4.50, 5.80, "scheduled"),
    ("PL-20261018-02", "Tottenham", "West Ham", "PL", "2026-10-18 14:00:00", 1.75, 4.00, 4.30, "scheduled"),
    ("PL-20261018-03", "Brighton", "Everton", "PL", "2026-10-18 14:00:00", 1.80, 3.80, 4.20, "scheduled"),
    ("PL-20261018-04", "Crystal Palace", "Wolves", "PL", "2026-10-18 14:00:00", 2.05, 3.35, 3.65, "scheduled"),
    ("PL-20261018-05", "Bournemouth", "Fulham", "PL", "2026-10-18 16:30:00", 2.15, 3.40, 3.30, "scheduled"),

    # La Liga
    ("PD-20261018-01", "Real Madrid", "Sevilla", "PD", "2026-10-18 20:00:00", 1.30, 5.50, 9.00, "scheduled"),
    ("PD-20261018-02", "Villarreal", "Real Sociedad", "PD", "2026-10-18 15:15:00", 2.10, 3.40, 3.40, "scheduled"),
    ("PD-20261018-03", "Valencia", "Real Betis", "PD", "2026-10-18 17:30:00", 2.45, 3.25, 2.95, "scheduled"),
    ("PD-20261018-04", "Celta Vigo", "Espanyol", "PD", "2026-10-18 13:00:00", 1.88, 3.50, 4.10, "scheduled"),
    ("PD-20261018-05", "Getafe", "Osasuna", "PD", "2026-10-18 17:30:00", 2.40, 2.95, 3.30, "scheduled"),

    # Serie A
    ("SA-20261018-01", "Roma", "Inter", "SA", "2026-10-18 19:45:00", 3.10, 3.40, 2.25, "scheduled"),
    ("SA-20261018-02", "Napoli", "Fiorentina", "SA", "2026-10-18 14:00:00", 1.68, 3.85, 4.90, "scheduled"),
    ("SA-20261018-03", "Lazio", "Atalanta", "SA", "2026-10-18 17:00:00", 2.50, 3.40, 2.75, "scheduled"),
    ("SA-20261018-04", "Torino", "Genoa", "SA", "2026-10-18 11:30:00", 2.05, 3.15, 3.90, "scheduled"),
    ("SA-20261018-05", "Bologna", "Cagliari", "SA", "2026-10-18 14:00:00", 1.72, 3.65, 4.80, "scheduled"),

    # Bundesliga
    ("BL1-20261018-01", "Dortmund", "RB Leipzig", "BL1", "2026-10-18 16:30:00", 2.10, 3.75, 3.20, "scheduled"),
    ("BL1-20261018-02", "Frankfurt", "Wolfsburg", "BL1", "2026-10-18 14:30:00", 1.95, 3.80, 3.60, "scheduled"),
    ("BL1-20261018-03", "Freiburg", "Mainz", "BL1", "2026-10-18 18:30:00", 1.82, 3.70, 4.20, "scheduled"),
    ("BL1-20261018-04", "Hoffenheim", "Augsburg", "BL1", "2026-10-18 14:30:00", 2.00, 3.80, 3.45, "scheduled"),
    ("BL1-20261018-05", "Werder Bremen", "Borussia Monchengladbach", "BL1", "2026-10-18 16:30:00", 2.25, 3.60, 3.00, "scheduled"),

    # Ligue 1
    ("FL1-20261018-01", "Marseille", "Lyon", "FL1", "2026-10-18 19:45:00", 1.90, 3.75, 3.80, "scheduled"),
    ("FL1-20261018-02", "Lille", "Rennes", "FL1", "2026-10-18 16:05:00", 1.85, 3.60, 4.10, "scheduled"),
    ("FL1-20261018-03", "Nice", "Strasbourg", "FL1", "2026-10-18 14:00:00", 1.78, 3.65, 4.40, "scheduled"),
    ("FL1-20261018-04", "Toulouse", "Montpellier", "FL1", "2026-10-18 14:00:00", 1.95, 3.55, 3.80, "scheduled"),
    ("FL1-20261018-05", "Reims", "Nantes", "FL1", "2026-10-18 14:00:00", 2.05, 3.35, 3.70, "scheduled"),

    # 2026-10-19: Monday Night Football
    ("PL-20261019-01", "West Ham", "Brentford", "PL", "2026-10-19 20:00:00", 2.05, 3.50, 3.45, "scheduled"),
    ("PD-20261019-01", "Girona", "Mallorca", "PD", "2026-10-19 20:00:00", 1.80, 3.50, 4.50, "scheduled"),
    ("SA-20261019-01", "Verona", "Parma", "SA", "2026-10-19 19:45:00", 2.30, 3.25, 3.15, "scheduled"),
    ("BL1-20261019-01", "St. Pauli", "Heidenheim", "BL1", "2026-10-19 19:30:00", 2.20, 3.40, 3.25, "scheduled"),
    ("FL1-20261019-01", "Auxerre", "Brest", "FL1", "2026-10-19 19:45:00", 2.45, 3.20, 2.95, "scheduled"),

    # Future Marquee Planning Fixtures (Oct 24 - Nov 4)
    ("PL-20261024-01", "Arsenal", "Chelsea", "PL", "2026-10-24 17:30:00", 1.78, 3.75, 4.30, "scheduled"),
    ("PL-20261024-02", "Manchester City", "Liverpool", "PL", "2026-10-24 12:30:00", 1.95, 3.85, 3.60, "scheduled"),
    ("PD-20261024-01", "Real Madrid", "Barcelona", "PD", "2026-10-24 20:00:00", 2.05, 3.80, 3.30, "scheduled"),
    ("BL1-20261024-01", "Bayern Munich", "Dortmund", "BL1", "2026-10-24 17:30:00", 1.45, 4.80, 6.00, "scheduled"),
    ("SA-20261024-01", "Inter", "Juventus", "SA", "2026-10-24 19:45:00", 1.85, 3.40, 4.30, "scheduled"),
    ("FL1-20261024-01", "Marseille", "Paris Saint Germain", "FL1", "2026-10-24 20:00:00", 3.80, 4.00, 1.82, "scheduled"),

    ("CL-20261028-01", "Arsenal", "Sporting CP", "CL", "2026-10-28 20:00:00", 1.35, 5.00, 8.50, "scheduled"),
    ("CL-20261028-02", "Manchester City", "Real Madrid", "CL", "2026-10-28 20:00:00", 1.88, 3.80, 3.85, "scheduled"),
    ("CL-20261028-03", "Barcelona", "Bayern Munich", "CL", "2026-10-28 20:00:00", 2.20, 3.75, 3.00, "scheduled"),
    ("CL-20261028-04", "Liverpool", "Bayer Leverkusen", "CL", "2026-10-28 20:00:00", 1.65, 4.10, 4.80, "scheduled"),
    ("CL-20261028-05", "Chelsea", "Inter", "CL", "2026-10-28 20:00:00", 2.15, 3.40, 3.35, "scheduled"),

    ("PL-20261101-01", "Newcastle", "Arsenal", "PL", "2026-11-01 13:30:00", 2.70, 3.50, 2.50, "scheduled"),
    ("PL-20261101-02", "Bournemouth", "Manchester City", "PL", "2026-11-01 16:00:00", 8.00, 5.20, 1.34, "scheduled"),
    ("PD-20261101-01", "Barcelona", "Espanyol", "PD", "2026-11-01 16:15:00", 1.20, 6.80, 12.00, "scheduled"),
    ("BL1-20261101-01", "Bayern Munich", "Union Berlin", "BL1", "2026-11-01 15:30:00", 1.18, 7.50, 14.00, "scheduled"),
]

inserted = 0
for m in fixtures_to_add:
    c.execute("SELECT id FROM historical_matches WHERE external_id = ?", (m[0],))
    if not c.fetchone():
        c.execute("""
            INSERT INTO historical_matches (
                external_id, home_team, away_team, league_id, match_date, home_odds, draw_odds, away_odds, status,
                home_possession_avg, away_possession_avg, home_shots_per_game, away_shots_per_game,
                home_shots_conceded_per_game, away_shots_conceded_per_game, home_home_scoring_rate,
                away_away_scoring_rate, home_home_conceding_rate, away_away_conceding_rate,
                home_recent_goals_scored, away_recent_goals_scored, home_recent_goals_conceded,
                away_recent_goals_conceded, home_corners, away_corners, home_cards, away_cards,
                home_penalties, away_penalties
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                52.0, 48.0, 13.5, 11.2, 10.5, 13.0, 1.85, 1.25, 0.95, 1.45,
                '[2,1,3,2,1]', '[1,0,2,1,0]', '[0,1,1,2,0]', '[1,2,0,1,2]',
                5.5, 4.5, 1.8, 2.2, 0, 0
            )
        """, m)
        inserted += 1

conn.commit()
print(f"Successfully inserted {inserted} marquee fixtures across multi-dates!")
conn.close()
