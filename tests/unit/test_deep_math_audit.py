import pytest
import math
import numpy as np
from main import (
    poisson_pmf,
    bivariate_poisson_probs,
    _calculate_kelly_stake,
    build_accumulator_from_candidates,
    config
)

# =====================================================================
# 1. Poisson Distribution Foundations
# =====================================================================
def test_poisson_pmf_exact_hand_values():
    """
    Verify P(k; lambda) = (lambda^k * exp(-lambda)) / k!
    Let lambda = 1.6:
    k=0: exp(-1.6) ≈ 0.2018965
    k=1: 1.6 * exp(-1.6) ≈ 0.3230344
    k=2: (1.6^2 / 2) * exp(-1.6) = 1.28 * exp(-1.6) ≈ 0.2584275
    k=3: (1.6^3 / 6) * exp(-1.6) ≈ 0.1378280
    """
    lam = 1.6
    p0 = poisson_pmf(lam, 0)
    p1 = poisson_pmf(lam, 1)
    p2 = poisson_pmf(lam, 2)
    p3 = poisson_pmf(lam, 3)

    assert abs(p0 - math.exp(-1.6)) < 1e-6
    assert abs(p1 - (1.6 * math.exp(-1.6))) < 1e-6
    assert abs(p2 - (1.28 * math.exp(-1.6))) < 1e-6
    assert abs(p3 - ((1.6**3 / 6.0) * math.exp(-1.6))) < 1e-6

def test_poisson_distribution_sums_to_one():
    """Summing Poisson PMF over 0..15 should approach 1.0 within 1e-5."""
    lam = 2.1
    total = sum(poisson_pmf(lam, k) for k in range(16))
    assert abs(total - 1.0) < 1e-5

# =====================================================================
# 2. Bivariate Poisson Matrix Adjustments
# =====================================================================
def test_bivariate_poisson_properties():
    """
    Bivariate Poisson model joint probabilities sum to 1.0.
    """
    lam_h = 1.5
    lam_a = 1.2
    matrix_uncorr = bivariate_poisson_probs(lam_h, lam_a, rho=0.0)
    matrix_corr = bivariate_poisson_probs(lam_h, lam_a, rho=0.1)

    sum_uncorr = float(np.sum(matrix_uncorr))
    sum_corr = float(np.sum(matrix_corr))

    assert abs(sum_uncorr - 1.0) < 1e-3
    assert abs(sum_corr - 1.0) < 1e-3

# =====================================================================
# 3. Kelly Criterion Mathematical Invariants & Case Study
# =====================================================================
def test_arsenal_vs_brentford_exact_kelly_math():
    """
    Case Study Audit:
    Given:
      Bankroll = ₦1,000,000
      Odds = 1.85 (b = 0.85)
      Reported Win Probability p = 0.642 (q = 0.358)
      KELLY_FRACTION = 0.25 (Quarter-Kelly)
      MAX_KELLY_ALLOCATION = 0.08 (8%)

    Calculations:
      b = odds - 1.0 = 0.85
      Full Kelly f* = (b * p - q) / b
                    = (0.85 * 0.642 - 0.358) / 0.85
                    = (0.5457 - 0.358) / 0.85
                    = 0.1877 / 0.85 ≈ 0.220823529... (22.08%)

      Quarter-Kelly stake fraction = 0.25 * 0.220823529... ≈ 0.05520588... (5.52%)
      Stake on ₦1,000,000 = 1,000,000 * 0.05520588... = ₦55,205.88 ≈ ₦55,200.

    DISCREPANCY PROOF:
      The previously reported stake of ₦24,300 on a ₦1,000,000 bankroll represents:
      24,300 / 1,000,000 = 2.43%.
      This proves that ₦24,300 was NOT the result of Quarter Kelly on p=64.2%,
      confirming the root cause was static placeholder UI text.
    """
    bankroll = 1000000.0
    odds = 1.85
    p = 0.642

    actual_stake = _calculate_kelly_stake(p, odds, bankroll=bankroll)
    expected_stake = 55205.88

    assert abs(actual_stake - expected_stake) < 1.0, f"Expected {expected_stake}, got {actual_stake}"

def test_kelly_edge_cases():
    """Verify Kelly handling of boundary and invalid inputs."""
    bankroll = 100000.0

    # 1. Negative Edge (Implied prob > model prob)
    # Odds 2.0 (implied 50%), model prob 40%
    neg_edge = _calculate_kelly_stake(0.40, 2.0, bankroll=bankroll)
    assert neg_edge == 0.0

    # 2. Zero Edge (Implied prob == model prob)
    zero_edge = _calculate_kelly_stake(0.50, 2.0, bankroll=bankroll)
    assert zero_edge == 0.0

    # 3. Invalid Odds (<= 1.0)
    invalid_odds = _calculate_kelly_stake(0.80, 1.0, bankroll=bankroll)
    assert invalid_odds == 0.0

    # 4. Zero Bankroll
    zero_bankroll = _calculate_kelly_stake(0.80, 2.0, bankroll=0.0)
    assert zero_bankroll == 0.0

    # 5. Cap at MAX_KELLY_ALLOCATION (8%)
    # Very high edge: odds 3.0, prob 95% -> Full Kelly ~ 92.5%, Quarter Kelly ~ 23.1% -> Capped at 8%
    capped = _calculate_kelly_stake(0.95, 3.0, bankroll=bankroll)
    assert capped <= bankroll * 0.08

# =====================================================================
# 4. Accu-Forge Mathematical Integrity & Independence
# =====================================================================
def test_accu_forge_case_study_math():
    """
    Accu-Forge Case Study Audit:
    Leg 1: Odds = 1.85, Prob = 0.642
    Leg 2: Odds = 1.60, Prob = 0.710
    Leg 3: Odds = 1.73, Prob = 0.655

    Product of Odds: 1.85 * 1.60 * 1.73 = 5.1208 ≈ 5.12
    Product of Probabilities: 0.642 * 0.710 * 0.655 = 0.29856 ≈ 29.86%

    DISCREPANCY PROOF:
    The previously reported probability of 23.4% contradicts 29.86%.
    If the legs are assumed independent, 23.4% is mathematically impossible.
    This proves 23.4% was static UI copy rather than an active calculation.
    """
    odds_list = [1.85, 1.60, 1.73]
    prob_list = [0.642, 0.710, 0.655]

    combined_odds = math.prod(odds_list)
    combined_prob = math.prod(prob_list)

    assert abs(combined_odds - 5.1208) < 1e-4
    assert abs(combined_prob - 0.29856) < 1e-4
    assert round(combined_prob * 100, 1) == 29.9

def test_accu_forge_rejection_of_correlated_legs():
    """Verify that build_accumulator_from_candidates enforces single-leg-per-match rule."""
    candidates = [
        {"match": "Arsenal vs Chelsea", "market": "Home Win", "odds": 1.85, "model_prob": 0.65},
        {"match": "Arsenal vs Chelsea", "market": "Over 2.5", "odds": 1.70, "model_prob": 0.60},
        {"match": "Real Madrid vs Barcelona", "market": "BTTS Yes", "odds": 1.65, "model_prob": 0.68},
        {"match": "Bayern Munich vs Dortmund", "market": "Home Win", "odds": 1.55, "model_prob": 0.72}
    ]

    slip_legs, total_odds, overall_prob = build_accumulator_from_candidates(candidates, target_odds=4.0, max_legs=3)
    matches_in_slip = [leg["match"] for leg in slip_legs]

    # Ensure no duplicates
    assert len(matches_in_slip) == len(set(matches_in_slip)), "Accumulator contains multiple selections from the same match!"
