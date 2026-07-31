from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EloConfig:
    starting_rating: float
    k_factor: float
    mov_multiplier_divisor: float
    mov_multiplier_scale: float
    home_ground_advantage: float
    season_regression_factor: float
    season_regression_target: float


def expected_home_win_probability(
    home_rating: float, away_rating: float, home_ground_advantage: float
) -> float:
    """Standard logistic Elo win-probability formula, 400-point scale."""
    rating_diff = (home_rating + home_ground_advantage) - away_rating
    return 1.0 / (1.0 + 10.0 ** (-rating_diff / 400.0))


def margin_of_victory_multiplier(
    margin: int, winner_rating_edge: float, divisor: float, scale: float
) -> float:
    """FiveThirtyEight-style margin-of-victory damping: amplifies upsets,
    damps an already-big-favorite winning by a lot. `winner_rating_edge` is
    the winning team's pre-game rating advantage (their rating minus the
    loser's, home ground advantage already folded in if applicable) —
    a large positive edge means the win was expected, so it's damped more.
    """
    return math.log(abs(margin) + 1) * (divisor / (max(winner_rating_edge, 0) * scale + divisor))


def update_ratings(
    home_rating: float, away_rating: float, home_score: int, away_score: int, config: EloConfig,
) -> "tuple[float, float]":
    """One Elo update for a single completed match. Returns (new_home_rating, new_away_rating).

    Zero-sum by construction (whatever the home team gains, the away team
    loses), consistent with standard Elo — ratings only move relative to
    each other, not in some independent per-team direction.
    """
    expected_home = expected_home_win_probability(home_rating, away_rating, config.home_ground_advantage)
    margin = home_score - away_score

    if margin > 0:
        actual_home = 1.0
        winner_edge = (home_rating + config.home_ground_advantage) - away_rating
        mov = margin_of_victory_multiplier(
            margin, winner_edge, config.mov_multiplier_divisor, config.mov_multiplier_scale
        )
    elif margin < 0:
        actual_home = 0.0
        winner_edge = away_rating - (home_rating + config.home_ground_advantage)
        mov = margin_of_victory_multiplier(
            margin, winner_edge, config.mov_multiplier_divisor, config.mov_multiplier_scale
        )
    else:
        # Rare in AFL, but real. There's no "margin" to damp/amplify by —
        # ln(0 + 1) is 0, which would zero out rating movement entirely and
        # silently ignore how surprising the draw was. Use a neutral
        # multiplier instead so an unexpected draw still moves ratings.
        actual_home = 0.5
        mov = 1.0

    delta = config.k_factor * mov * (actual_home - expected_home)

    return home_rating + delta, away_rating - delta


def apply_season_regression(rating: float, config: EloConfig) -> float:
    """Pulls a rating partway back toward the regression target, applied
    once per team at the start of each new season (list changes, etc. mean
    inter-season strength regresses toward the mean more than in-season
    form does — see config.yaml for the reasoning).
    """
    target = config.season_regression_target
    return target + config.season_regression_factor * (rating - target)
