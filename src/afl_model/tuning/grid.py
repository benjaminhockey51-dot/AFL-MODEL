from __future__ import annotations

# Grid ranges chosen to bracket each parameter's current config.yaml value
# with room on both sides, not just nearby — a grid that only explores
# near the existing guess would just confirm the guess, not test it.
#
# Split into two independent stages, exploiting real structure in this
# architecture rather than brute-forcing every combination together:
#
# Stage A (win probability): elo.k_factor, home_ground_advantage, and
# season_regression_factor all change the walk-forward Elo trajectory
# itself, so each combination needs its own walk-forward recomputation
# (the "outer" grid). form/rest/travel are prediction-time-only
# adjustments that never feed back into any stored rating, so for a
# *fixed* outer combination, every combination of them can be scored
# against the same walk-forward result without recomputing it (the
# "inner" grid) — this is what keeps a ~45,000-combination search
# tractable without approximating anything.
ELO_K_FACTOR_GRID = [10.0, 15.0, 20.0, 25.0, 30.0, 40.0]
HOME_GROUND_ADVANTAGE_GRID = [0.0, 15.0, 25.0, 35.0, 45.0, 55.0, 70.0]
SEASON_REGRESSION_FACTOR_GRID = [0.3, 0.5, 0.65, 0.75, 0.85, 1.0]

FORM_ELO_SCALE_GRID = [0.0, 10.0, 25.0, 50.0, 75.0, 100.0]
REST_ELO_SCALE_PER_DAY_GRID = [0.0, 1.0, 2.0, 3.0, 5.0]
TRAVEL_ELO_SCALE_PER_100KM_GRID = [0.0, 0.5, 1.0, 1.5, 2.5, 4.0]

# Stage B (margin/total): attack_defence.k_factor and
# league_avg_score_ewma_alpha only affect points-space predictions in this
# architecture (verified during Stage 5's ablation study), so this stage
# is scored on margin MAE alone, independent of Stage A's outcome.
ATTACK_DEFENCE_K_FACTOR_GRID = [0.05, 0.10, 0.15, 0.20, 0.30]
LEAGUE_AVG_SCORE_EWMA_ALPHA_GRID = [0.01, 0.02, 0.05, 0.10]

# Deliberately NOT tuned, and why:
#   elo.starting_rating / season_regression_target — an arbitrary anchor;
#     only their relationship to each other matters, and they're already
#     equal (1500). Grid-searching an anchor point is meaningless.
#   elo.mov_multiplier_divisor / mov_multiplier_scale — a coupled pair
#     borrowed from FiveThirtyEight's published NFL/NBA methodology;
#     decoupling them into a 2D grid without a principled reason to
#     believe AFL's margin distribution needs different shape constants
#     would be searching for the sake of searching.
#   prediction.line_rounding — a display/formatting convention (bookmaker
#     lines are set in half-points), not a predictive parameter; doesn't
#     change win probability, margin, or total at all.
#   prediction.confidence_maturity_games — affects only the confidence
#     display value, which has no ground truth to score against.
