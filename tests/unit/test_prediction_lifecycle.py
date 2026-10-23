import pytest
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import select
from main import AsyncSessionLocal, PredictionLog, BetSlip, HistoricalMatch, User, hash_password

@pytest.mark.asyncio
async def test_prediction_log_and_betslip_lifecycle():
    """
    Test the complete prediction logging and slip settlement lifecycle:
    1. Log a prediction in PredictionLog with probability distribution.
    2. Place and save a BetSlip with odds and stake.
    3. Simulate match settlement.
    4. Transition slip status to WON or LOST and verify payout accounting.
    """
    unique_ext = f"FX-{uuid.uuid4().hex[:8]}"
    unique_pred = f"PRED-{uuid.uuid4().hex[:8]}"
    unique_slip = f"TIP-{uuid.uuid4().hex[:6].upper()}"

    async with AsyncSessionLocal() as session:
        # 1. Create or get user
        user = User(
            username=f"bettor_{uuid.uuid4().hex[:6]}",
            email=f"bettor_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("securepw123"),
            bankroll=1000000.0,
            subscription_tier="pro"
        )
        session.add(user)
        await session.flush()

        # 2. Seed match fixture
        fixture = HistoricalMatch(
            external_id=unique_ext,
            home_team="Arsenal",
            away_team="Brentford",
            league_id="PL",
            match_date=datetime.now(timezone.utc),
            home_odds=1.85,
            draw_odds=3.60,
            away_odds=4.20
        )
        session.add(fixture)

        # 3. Save PredictionLog entry
        pred_entry = PredictionLog(
            prediction_id=unique_pred,
            endpoint="home-win",
            home_team="Arsenal",
            away_team="Brentford",
            fixture_json=json.dumps({"external_id": unique_ext, "odds": 1.85}),
            raw_probs_json=json.dumps({"home_prob": 0.5465, "draw_prob": 0.25, "away_prob": 0.2035}),
            market_probs_json=json.dumps({"market": "home-win", "edge": 0.006}),
            confidence_percent=54.7,
            processed=False
        )
        session.add(pred_entry)

        # 4. Save BetSlip
        stake = 50000.0
        odds = 1.85
        potential_payout = round(stake * odds, 2)

        slip = BetSlip(
            user_id=user.id,
            slip_code=unique_slip,
            total_odds=odds,
            stake=stake,
            potential_payout=potential_payout,
            items=json.dumps([{"match": "Arsenal vs Brentford", "market": "Home Win", "odds": odds}]),
            status="active"
        )
        session.add(slip)
        await session.commit()
        slip_id = slip.id
        pred_id = pred_entry.id

    # 5. Settlement: Arsenal wins 2-1 -> Condition satisfied -> WON
    async with AsyncSessionLocal() as session:
        slip_res = await session.execute(select(BetSlip).where(BetSlip.id == slip_id))
        slip_obj = slip_res.scalar_one()
        assert slip_obj.status == "active"

        # Update settlement
        slip_obj.status = "won"

        pred_res = await session.execute(select(PredictionLog).where(PredictionLog.id == pred_id))
        pred_obj = pred_res.scalar_one()
        pred_obj.processed = True

        await session.commit()

    # 6. Verify settled state
    async with AsyncSessionLocal() as session:
        slip_res = await session.execute(select(BetSlip).where(BetSlip.id == slip_id))
        settled_slip = slip_res.scalar_one()
        assert settled_slip.status == "won"
        assert settled_slip.potential_payout == 92500.0
        pnl = settled_slip.potential_payout - settled_slip.stake
        assert pnl == 42500.0 # ₦42,500 net profit
