import math
import numpy as np
import pytest
from main import (
    bivariate_poisson_probs,
    poisson_pmf,
    compute_form_score,
    compute_streak,
    estimate_draw_probability,
    _calculate_kelly_stake,
    AdvancedPredictor
)

def test_poisson_pmf_math():
    """Verify Poisson PMF implementation against standard formula."""
    # P(k=0; lambda=2.0) = exp(-2) = 0.135335
    p0 = poisson_pmf(2.0, 0)
    assert math.isclose(p0, math.exp(-2.0), rel_tol=1e-5)
    
    # P(k=1; lambda=2.0) = 2 * exp(-2) = 0.270670
    p1 = poisson_pmf(2.0, 1)
    assert math.isclose(p1, 2.0 * math.exp(-2.0), rel_tol=1e-5)
    
    # Boundary: lambda=0
    assert poisson_pmf(0.0, 0) == 1.0
    assert poisson_pmf(0.0, 1) == 0.0

def test_bivariate_poisson_probability_invariants():
    """Verify that bivariate Poisson joint distribution satisfies mathematical invariants."""
    for lam1, lam2, rho in [(1.8, 1.2, 0.08), (2.5, 0.8, 0.05), (0.5, 0.5, 0.02), (3.8, 3.2, 0.08)]:
        joint = bivariate_poisson_probs(lam1, lam2, rho, max_goals=10)
        
        # 1. Non-negativity
        assert np.all(joint >= 0.0), f"Negative probabilities found in joint matrix for ({lam1}, {lam2})"
        
        # 2. Total mass sum (truncation at max_goals=10 loses < 0.2% mass)
        total_mass = float(np.sum(joint))
        assert 0.99 <= total_mass <= 1.0001, f"Total probability mass {total_mass} outside expected [0.99, 1.0]"
        
        # 3. 1X2 Exhaustive decomposition
        h_win = float(sum(joint[i, j] for i in range(11) for j in range(11) if i > j))
        draw = float(sum(joint[i, j] for i in range(11) for j in range(11) if i == j))
        a_win = float(sum(joint[i, j] for i in range(11) for j in range(11) if i < j))
        sum_1x2 = h_win + draw + a_win
        assert math.isclose(sum_1x2, total_mass, rel_tol=1e-6)

def test_advanced_predictor_prob_consistency():
    """Verify consistency between total goals, BTTS, and Double Chance derived probabilities."""
    pred = AdvancedPredictor()
    fixture = {
        'league_avg_goals': 2.7,
        'home': {
            'goals_scored_home_pg': 1.8,
            'xg_for_home_pg': 1.7,
            'goals_conceded_home_pg': 0.9,
            'xg_against_home_pg': 1.0,
            'recent_xg_records': [(1.8, 5), (2.1, 12)],
            'days_since_last_match': 6
        },
        'away': {
            'goals_scored_away_pg': 1.2,
            'xg_for_away_pg': 1.1,
            'goals_conceded_away_pg': 1.6,
            'xg_against_away_pg': 1.5,
            'recent_xg_records': [(1.0, 4), (1.3, 10)],
            'days_since_last_match': 7
        },
        'h2h': {'avg_goals_last5': 2.8, 'home_scored_avg_vs_opp': 1.6, 'away_scored_avg_vs_opp': 1.2},
        'match_context': {}
    }
    
    probs = pred._raw_probabilities(fixture)
    
    # 1. Check Double Chance agreement with 1X2
    assert math.isclose(probs['double_chance_1x'], probs['home_win'] + probs['draw'], rel_tol=1e-5)
    assert math.isclose(probs['double_chance_x2'], probs['away_win'] + probs['draw'], rel_tol=1e-5)
    
    # 2. Check Over / Under complementary sums
    assert math.isclose(probs['over_1_5'] + probs['under_1_5'], 1.0, abs_tol=1e-5)
    assert math.isclose(probs['over_2_5'] + probs['under_2_5'], 1.0, abs_tol=1e-5)
    assert math.isclose(probs['over_3_5'] + probs['under_3_5'], 1.0, abs_tol=1e-5)
    assert math.isclose(probs['over_4_5'] + probs['under_4_5'], 1.0, abs_tol=1e-5)
    
    # 3. Check BTTS complementary sum
    assert math.isclose(probs['btts_yes'] + probs['btts_no'], 1.0, abs_tol=1e-5)
    
    # 4. Check ordering: Over 1.5 >= Over 2.5 >= Over 3.5 >= Over 4.5
    assert probs['over_1_5'] >= probs['over_2_5'] >= probs['over_3_5'] >= probs['over_4_5']

def test_form_score_and_streak_math():
    """Verify form scoring logic and streak calculation."""
    # Perfect form
    assert compute_form_score("WWWWW") == 0.95  # Clamped maximum
    # Total loss
    assert compute_form_score("LLLLL") == 0.05  # Clamped minimum
    # Neutral draws
    assert math.isclose(compute_form_score("DDDDD"), 0.50, abs_tol=1e-5)
    # Empty or short string default
    assert compute_form_score("") == 0.50
    assert compute_form_score("W") == 0.50
    
    # Streaks
    assert compute_streak("WWWDW") == 3
    assert compute_streak("LLWDL") == -2
    assert compute_streak("DWWW") == 0

def test_kelly_stake_formula():
    """Verify Kelly Criterion stake calculation."""
    bankroll = 100000.0
    
    # Case 1: Positive edge (p=0.60, odds=2.00, b=1.0) -> Kelly = (0.60*1 - 0.40) / 1 = 0.20
    # Half-Kelly or full Kelly
    stake = _calculate_kelly_stake(0.60, 2.0, bankroll)
    assert stake > 0, "Kelly stake should be positive for positive EV"
    assert stake <= bankroll * 0.25, "Kelly stake should respect reasonable bankroll bounds"
    
    # Case 2: Negative edge (p=0.40, odds=2.00) -> Kelly is negative or zero
    assert _calculate_kelly_stake(0.40, 2.0, bankroll) == 0.0
    
    # Case 3: Invalid odds (odds <= 1.0)
    assert _calculate_kelly_stake(0.80, 1.0, bankroll) == 0.0
    assert _calculate_kelly_stake(0.80, 0.5, bankroll) == 0.0
    
    # Case 4: None bankroll
    assert _calculate_kelly_stake(0.70, 2.0, None) == 0.0
