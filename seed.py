#!/usr/bin/env python3
"""
Seed script for TipAlgo v21 Database
Populates users, league tables, historical matches, upcoming fixtures, and H2H records.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from main import (
    AsyncSessionLocal, init_db, User, LeagueTable,
    HistoricalMatch, HeadToHead, FeatureStore,
    hash_password, config
)

async def seed():
    print("Initializing database tables...")
    await init_db()

    async with AsyncSessionLocal() as db:
        # 1. Check or Seed Users
        res = await db.execute(select(User).where(User.email == "demo@tipalgo.com"))
        demo_user = res.scalar_one_or_none()
        if not demo_user:
            demo_user = User(
                email="demo@tipalgo.com",
                username="demopro",
                password_hash=hash_password("demopassword123"),
                bankroll=1000000.0,
                subscription_tier="pro",
                subscription_expiry=datetime.now(timezone.utc) + timedelta(days=365),
                is_admin=False,
                personality_mode="PROFESSIONAL",
                locale="NG"
            )
            db.add(demo_user)
            print("Created demo Pro user: demo@tipalgo.com / demopassword123")

        res_admin = await db.execute(select(User).where(User.email == "admin@tipalgo.com"))
        admin_user = res_admin.scalar_one_or_none()
        if not admin_user:
            admin_user = User(
                email="admin@tipalgo.com",
                username="admin",
                password_hash=hash_password("adminpassword123"),
                bankroll=5000000.0,
                subscription_tier="pro",
                subscription_expiry=datetime.now(timezone.utc) + timedelta(days=365),
                is_admin=True,
                personality_mode="PROFESSIONAL",
                locale="UK"
            )
            db.add(admin_user)
            print("Created admin user: admin@tipalgo.com / adminpassword123")

        # 2. Seed Premier League (PL) Table
        pl_teams = [
            ("Arsenal", 1, 30, 22, 5, 3, 75, 24, 51, 71, "WWWDW", 15, 12, 2, 1, 40, 13, 15, 10, 3, 2, 35, 11, 0.45, 0.40, 0.10, 0.15, 6.8, 5.2, 1.8, 1.9, 2.35, 0.85, 1.95, 0.90),
            ("Manchester City", 2, 29, 21, 5, 3, 72, 25, 47, 68, "WDWWW", 15, 12, 3, 0, 42, 12, 14, 9, 2, 3, 30, 13, 0.50, 0.35, 0.05, 0.10, 7.5, 4.8, 1.5, 1.7, 2.45, 0.80, 2.10, 0.95),
            ("Liverpool", 3, 30, 20, 7, 3, 70, 28, 42, 67, "WWDLW", 15, 12, 3, 0, 43, 14, 15, 8, 4, 3, 27, 14, 0.40, 0.30, 0.08, 0.15, 7.1, 5.0, 1.9, 2.0, 2.40, 0.95, 1.85, 1.05),
            ("Aston Villa", 4, 31, 18, 5, 8, 66, 46, 20, 59, "LWDWL", 16, 11, 2, 3, 40, 21, 15, 7, 3, 5, 26, 25, 0.30, 0.20, 0.15, 0.20, 5.8, 4.4, 2.2, 2.4, 1.90, 1.35, 1.55, 1.45),
            ("Tottenham", 5, 30, 17, 6, 7, 62, 44, 18, 57, "WLWWL", 15, 11, 0, 4, 34, 20, 15, 6, 6, 3, 28, 24, 0.25, 0.20, 0.10, 0.15, 6.2, 4.8, 2.3, 2.5, 1.85, 1.40, 1.60, 1.50),
            ("Chelsea", 6, 30, 14, 8, 8, 58, 45, 13, 50, "WDDWW", 15, 9, 3, 3, 31, 20, 15, 5, 5, 5, 27, 25, 0.30, 0.20, 0.20, 0.25, 5.9, 4.7, 2.5, 2.6, 1.80, 1.30, 1.50, 1.45),
            ("Newcastle", 7, 31, 15, 5, 11, 65, 52, 13, 50, "WWLDW", 16, 10, 3, 3, 39, 20, 15, 5, 2, 8, 26, 32, 0.35, 0.15, 0.15, 0.25, 5.5, 4.2, 2.1, 2.3, 1.95, 1.25, 1.40, 1.70),
            ("Manchester United", 8, 30, 15, 4, 11, 45, 46, -1, 49, "LDWLW", 15, 8, 2, 5, 24, 22, 15, 7, 2, 6, 21, 24, 0.30, 0.25, 0.25, 0.20, 5.4, 4.5, 2.2, 2.4, 1.50, 1.45, 1.35, 1.55),
            ("West Ham", 9, 31, 13, 9, 9, 50, 55, -5, 48, "DDLWD", 16, 6, 6, 4, 26, 24, 15, 7, 3, 5, 24, 31, 0.20, 0.20, 0.20, 0.25, 4.8, 4.0, 2.0, 2.1, 1.45, 1.60, 1.30, 1.75),
            ("Brighton", 10, 30, 11, 10, 9, 51, 46, 5, 43, "LDWLD", 15, 7, 6, 2, 28, 19, 15, 4, 4, 7, 23, 27, 0.25, 0.15, 0.15, 0.30, 5.6, 4.6, 2.4, 2.5, 1.65, 1.35, 1.40, 1.65),
            ("Wolves", 11, 31, 12, 6, 13, 44, 49, -5, 42, "LLWDL", 15, 7, 3, 5, 23, 22, 16, 5, 3, 8, 21, 27, 0.20, 0.15, 0.25, 0.30, 4.6, 4.1, 2.6, 2.7, 1.35, 1.50, 1.25, 1.60),
            ("Bournemouth", 12, 30, 11, 8, 11, 44, 53, -9, 41, "WWWDW", 15, 6, 4, 5, 21, 22, 15, 5, 4, 6, 23, 31, 0.25, 0.15, 0.20, 0.30, 6.0, 4.8, 1.9, 2.1, 1.55, 1.50, 1.35, 1.80),
            ("Fulham", 13, 31, 11, 6, 14, 47, 51, -4, 39, "LWWDL", 16, 9, 1, 6, 31, 19, 15, 2, 5, 8, 16, 32, 0.35, 0.10, 0.20, 0.40, 5.2, 4.3, 2.1, 2.3, 1.60, 1.30, 1.20, 1.85),
            ("Crystal Palace", 14, 30, 7, 9, 14, 34, 50, -16, 30, "LDDLL", 15, 4, 4, 7, 21, 24, 15, 3, 5, 7, 13, 26, 0.20, 0.20, 0.30, 0.35, 4.5, 3.8, 1.8, 2.0, 1.25, 1.55, 1.10, 1.65),
            ("Brentford", 15, 31, 7, 7, 17, 45, 58, -13, 28, "DDLLD", 16, 4, 6, 6, 25, 30, 15, 3, 1, 11, 20, 28, 0.15, 0.10, 0.25, 0.35, 4.9, 4.2, 2.2, 2.3, 1.45, 1.70, 1.25, 1.80),
            ("Everton", 16, 31, 9, 8, 14, 32, 42, -10, 27, "DLLLD", 15, 4, 4, 7, 16, 18, 16, 5, 4, 7, 16, 24, 0.30, 0.25, 0.35, 0.40, 5.1, 4.0, 2.3, 2.4, 1.30, 1.40, 1.15, 1.55),
            ("Nottingham Forest", 17, 31, 7, 8, 16, 39, 56, -17, 25, "WDDLL", 15, 5, 4, 6, 21, 21, 16, 2, 4, 10, 18, 35, 0.20, 0.10, 0.30, 0.45, 4.2, 3.5, 2.4, 2.5, 1.35, 1.60, 1.10, 1.95),
            ("Luton", 18, 31, 5, 7, 19, 43, 65, -22, 22, "LLDLL", 15, 3, 3, 9, 21, 27, 16, 2, 4, 10, 22, 38, 0.10, 0.05, 0.25, 0.35, 5.2, 3.9, 1.9, 2.1, 1.40, 1.85, 1.20, 2.10),
            ("Burnley", 19, 31, 4, 7, 20, 32, 67, -35, 19, "DDWLD", 16, 2, 3, 11, 16, 35, 15, 2, 4, 9, 16, 32, 0.10, 0.10, 0.40, 0.45, 4.4, 3.6, 2.3, 2.5, 1.15, 1.95, 1.05, 2.05),
            ("Sheffield United", 20, 30, 3, 6, 21, 28, 80, -52, 15, "LDDLL", 15, 2, 3, 10, 17, 39, 15, 1, 3, 11, 11, 41, 0.10, 0.05, 0.45, 0.55, 3.8, 3.2, 2.5, 2.7, 1.05, 2.30, 0.90, 2.50),
        ]

        # Check existing table
        res_t = await db.execute(select(LeagueTable).where(LeagueTable.league_id == "PL"))
        existing_teams = {t.team_name for t in res_t.scalars().all()}

        for t in pl_teams:
            name, pos, pl, w, d, l, gf, ga, gd, pts, form, hp, hw, hd, hl, hgf, hga, ap, aw, ad, al, agf, aga, hcsp, acsp, hfsp, afsp, hca, aca, hcard, acard, xgh, xgah, xga, xgaa = t
            if name not in existing_teams:
                row = LeagueTable(
                    league_id="PL", team_name=name, position=pos, played=pl, wins=w, draws=d, losses=l,
                    goals_for=gf, goals_against=ga, goal_difference=gd, points=pts, last_5_form=form,
                    home_played=hp, home_wins=hw, home_draws=hd, home_losses=hl, home_goals_for=hgf, home_goals_against=hga,
                    away_played=ap, away_wins=aw, away_draws=ad, away_losses=al, away_goals_for=agf, away_goals_against=aga,
                    home_clean_sheet_pct=hcsp, away_clean_sheet_pct=acsp, home_failed_to_score_pct=hfsp, away_failed_to_score_pct=afsp,
                    home_corners_avg=hca, away_corners_avg=aca, home_cards_avg=hcard, away_cards_avg=acard,
                    xg_for_home=xgh, xg_against_home=xgah, xg_for_away=xga, xg_against_away=xgaa
                )
                db.add(row)
        print(f"Seeded {len(pl_teams)} Premier League teams.")

        # 3. Seed La Liga (PD) Table
        pd_teams = [
            ("Real Madrid", 1, 30, 23, 6, 1, 66, 20, 46, 75, "WDWWW", 15, 13, 2, 0, 37, 7, 15, 10, 4, 1, 29, 13, 0.55, 0.40, 0.05, 0.10, 6.5, 5.0, 1.7, 1.9, 2.40, 0.70, 2.05, 0.85),
            ("Barcelona", 2, 30, 20, 7, 3, 61, 34, 27, 67, "WWWDW", 16, 12, 1, 3, 36, 19, 14, 8, 6, 0, 25, 15, 0.45, 0.35, 0.10, 0.15, 6.8, 4.9, 2.2, 2.4, 2.25, 0.95, 1.90, 1.05),
            ("Girona", 3, 30, 20, 5, 5, 62, 36, 26, 65, "WLWLW", 15, 12, 2, 1, 38, 16, 15, 8, 3, 4, 24, 20, 0.35, 0.25, 0.10, 0.20, 5.4, 4.5, 2.0, 2.2, 2.05, 1.10, 1.65, 1.30),
            ("Atletico Madrid", 4, 30, 18, 4, 8, 56, 35, 21, 58, "WLLWW", 15, 13, 1, 1, 34, 13, 15, 5, 3, 7, 22, 22, 0.40, 0.25, 0.15, 0.20, 5.5, 4.6, 2.5, 2.7, 1.95, 1.00, 1.55, 1.35),
            ("Athletic Club", 5, 30, 16, 8, 6, 50, 28, 22, 56, "LWDWW", 15, 11, 3, 1, 36, 14, 15, 5, 5, 5, 14, 14, 0.45, 0.30, 0.15, 0.25, 5.9, 4.4, 2.3, 2.5, 1.85, 0.95, 1.35, 1.15),
        ]
        res_pd = await db.execute(select(LeagueTable).where(LeagueTable.league_id == "PD"))
        existing_pd = {t.team_name for t in res_pd.scalars().all()}
        for t in pd_teams:
            name, pos, pl, w, d, l, gf, ga, gd, pts, form, hp, hw, hd, hl, hgf, hga, ap, aw, ad, al, agf, aga, hcsp, acsp, hfsp, afsp, hca, aca, hcard, acard, xgh, xgah, xga, xgaa = t
            if name not in existing_pd:
                db.add(LeagueTable(
                    league_id="PD", team_name=name, position=pos, played=pl, wins=w, draws=d, losses=l,
                    goals_for=gf, goals_against=ga, goal_difference=gd, points=pts, last_5_form=form,
                    home_played=hp, home_wins=hw, home_draws=hd, home_losses=hl, home_goals_for=hgf, home_goals_against=hga,
                    away_played=ap, away_wins=aw, away_draws=ad, away_losses=al, away_goals_for=agf, away_goals_against=aga,
                    home_clean_sheet_pct=hcsp, away_clean_sheet_pct=acsp, home_failed_to_score_pct=hfsp, away_failed_to_score_pct=afsp,
                    home_corners_avg=hca, away_corners_avg=aca, home_cards_avg=hcard, away_cards_avg=acard,
                    xg_for_home=xgh, xg_against_home=xgah, xg_for_away=xga, xg_against_away=xgaa
                ))
        print("Seeded top La Liga teams.")

        # 4. Seed Historical Matches (Past 60+ matches for form / ML training)
        now = datetime.now(timezone.utc)
        res_hm = await db.execute(select(HistoricalMatch).limit(5))
        if len(res_hm.scalars().all()) < 10:
            hist_matches = [
                ("HM-001", "Arsenal", "Chelsea", 3, 1, "PL", now - timedelta(days=12), 1.55, 5.80, 4.30),
                ("HM-002", "Manchester City", "Liverpool", 1, 1, "PL", now - timedelta(days=15), 1.85, 3.90, 3.80),
                ("HM-003", "Liverpool", "Manchester United", 2, 0, "PL", now - timedelta(days=20), 1.40, 7.50, 5.20),
                ("HM-004", "Arsenal", "Newcastle", 4, 1, "PL", now - timedelta(days=25), 1.45, 6.50, 4.80),
                ("HM-005", "Aston Villa", "Tottenham", 1, 2, "PL", now - timedelta(days=18), 2.30, 2.90, 3.50),
                ("HM-006", "Chelsea", "Tottenham", 2, 0, "PL", now - timedelta(days=22), 2.20, 3.10, 3.60),
                ("HM-007", "Manchester City", "Arsenal", 0, 0, "PL", now - timedelta(days=28), 1.95, 3.80, 3.50),
                ("HM-008", "Newcastle", "Tottenham", 4, 0, "PL", now - timedelta(days=14), 2.40, 2.70, 3.70),
                ("HM-009", "Liverpool", "Brighton", 2, 1, "PL", now - timedelta(days=24), 1.35, 8.00, 5.50),
                ("HM-010", "Arsenal", "Aston Villa", 0, 2, "PL", now - timedelta(days=16), 1.30, 9.50, 5.80),
                ("HM-011", "Manchester City", "Luton", 5, 1, "PL", now - timedelta(days=17), 1.10, 22.0, 11.0),
                ("HM-012", "Chelsea", "Manchester United", 4, 3, "PL", now - timedelta(days=21), 2.00, 3.40, 3.90),
                ("HM-013", "Fulham", "Arsenal", 1, 2, "PL", now - timedelta(days=35), 5.50, 1.55, 4.20),
                ("HM-014", "Manchester United", "Liverpool", 2, 2, "PL", now - timedelta(days=23), 4.60, 1.65, 4.40),
                ("HM-015", "Tottenham", "Nottingham Forest", 3, 1, "PL", now - timedelta(days=26), 1.40, 7.00, 5.00),
                ("HM-016", "Bournemouth", "Manchester United", 2, 2, "PL", now - timedelta(days=19), 2.45, 2.70, 3.80),
                ("HM-017", "West Ham", "Tottenham", 1, 1, "PL", now - timedelta(days=30), 3.10, 2.15, 3.80),
                ("HM-018", "Brighton", "Arsenal", 0, 3, "PL", now - timedelta(days=27), 5.00, 1.65, 4.20),
                ("HM-019", "Crystal Palace", "Manchester City", 2, 4, "PL", now - timedelta(days=29), 9.00, 1.30, 5.50),
                ("HM-020", "Aston Villa", "Brentford", 3, 3, "PL", now - timedelta(days=29), 1.70, 4.60, 4.20),
                ("HM-021", "Everton", "Liverpool", 2, 0, "PL", now - timedelta(days=11), 6.50, 1.45, 4.80),
                ("HM-022", "Arsenal", "Chelsea", 5, 0, "PL", now - timedelta(days=10), 1.50, 6.00, 4.60),
                ("HM-023", "Tottenham", "Arsenal", 2, 3, "PL", now - timedelta(days=8), 3.60, 1.95, 4.00),
                ("HM-024", "Manchester City", "Wolves", 5, 1, "PL", now - timedelta(days=6), 1.12, 18.0, 9.50),
                ("HM-025", "Liverpool", "Tottenham", 4, 2, "PL", now - timedelta(days=5), 1.45, 5.80, 5.20),
                ("HM-026", "Chelsea", "West Ham", 5, 0, "PL", now - timedelta(days=5), 1.60, 5.00, 4.50),
                ("HM-027", "Real Madrid", "Barcelona", 3, 2, "PD", now - timedelta(days=12), 1.85, 3.80, 3.90),
                ("HM-028", "Atletico Madrid", "Athletic Club", 3, 1, "PD", now - timedelta(days=10), 1.95, 3.90, 3.50),
                ("HM-029", "Girona", "Barcelona", 4, 2, "PD", now - timedelta(days=6), 2.70, 2.45, 3.80),
                ("HM-030", "Real Madrid", "Cadiz", 3, 0, "PD", now - timedelta(days=7), 1.25, 11.0, 6.00),
            ]
            # duplicate with variations to exceed 50 for ScreenerModel training
            for i in range(30):
                h = pl_teams[i % len(pl_teams)][0]
                a = pl_teams[(i + 3) % len(pl_teams)][0]
                hist_matches.append((
                    f"HM-GEN-{i:03d}", h, a, (i % 4), ((i + 1) % 3), "PL",
                    now - timedelta(days=40 + i), 1.80 + (i % 5) * 0.2, 3.20, 3.50
                ))

            for m in hist_matches:
                db.add(HistoricalMatch(
                    external_id=m[0], home_team=m[1], away_team=m[2], home_score=m[3], away_score=m[4],
                    league_id=m[5], match_date=m[6], home_odds=m[7], away_odds=m[8], draw_odds=m[9],
                    status="finished", home_possession_avg=54.0, away_possession_avg=46.0,
                    home_shots_per_game=14.0, away_shots_per_game=10.0,
                    home_shots_conceded_per_game=10.0, away_shots_conceded_per_game=14.0
                ))
            print(f"Seeded {len(hist_matches)} historical matches for form and training.")

        # 5. Seed Upcoming Fixtures (Quarantined to TEST environment only - never leaked to production)
        import os, sys
        seed_test = os.getenv("SEED_TEST_FIXTURES", "false").lower() == "true" or "--test" in sys.argv
        if seed_test:
            res_fut = await db.execute(select(HistoricalMatch).where(HistoricalMatch.match_date > now))
            if len(res_fut.scalars().all()) < 5:
                upcoming = [
                    ("TEST-FUT-001", "Arsenal", "Chelsea", "PL", now + timedelta(days=2), 1.65, 4.80, 4.10),
                    ("TEST-FUT-002", "Manchester City", "Aston Villa", "PL", now + timedelta(days=2, hours=3), 1.30, 8.50, 5.50),
                    ("TEST-FUT-003", "Liverpool", "Tottenham", "PL", now + timedelta(days=3), 1.55, 5.20, 4.60),
                ]
                for u in upcoming:
                    db.add(HistoricalMatch(
                        external_id=u[0], home_team=u[1], away_team=u[2], league_id=u[3],
                        match_date=u[4], home_odds=u[5], away_odds=u[6], draw_odds=u[7],
                        status="scheduled", home_score=None, away_score=None
                    ))
                print(f"Seeded {len(upcoming)} test-only upcoming fixtures.")
        else:
            print("Production mode: Skipping artificial upcoming fixture generation. Live data will be populated by FootballDataService.")

        # 6. Seed Head-To-Head records
        res_h2h = await db.execute(select(HeadToHead).limit(5))
        if len(res_h2h.scalars().all()) < 5:
            h2h_list = [
                ("Arsenal", "Chelsea", now - timedelta(days=120), 3, 1, "PL", True, True),
                ("Chelsea", "Arsenal", now - timedelta(days=240), 2, 2, "PL", True, True),
                ("Arsenal", "Chelsea", now - timedelta(days=360), 3, 1, "PL", True, True),
                ("Chelsea", "Arsenal", now - timedelta(days=480), 0, 1, "PL", False, True),
                ("Arsenal", "Chelsea", now - timedelta(days=600), 2, 0, "PL", True, False),

                ("Manchester City", "Liverpool", now - timedelta(days=150), 1, 1, "PL", True, True),
                ("Liverpool", "Manchester City", now - timedelta(days=280), 1, 1, "PL", True, True),
                ("Manchester City", "Liverpool", now - timedelta(days=400), 4, 1, "PL", True, True),
                ("Liverpool", "Manchester City", now - timedelta(days=550), 1, 0, "PL", True, False),

                ("Real Madrid", "Barcelona", now - timedelta(days=160), 3, 2, "PD", True, True),
                ("Barcelona", "Real Madrid", now - timedelta(days=320), 1, 2, "PD", True, True),
                ("Real Madrid", "Barcelona", now - timedelta(days=450), 0, 1, "PD", False, True),
            ]
            for h in h2h_list:
                db.add(HeadToHead(
                    home_team=h[0], away_team=h[1], match_date=h[2],
                    home_score=h[3], away_score=h[4], league=h[5],
                    home_scored=h[6], away_scored=h[7]
                ))
            print("Seeded Head-to-Head records.")

        await db.commit()
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
