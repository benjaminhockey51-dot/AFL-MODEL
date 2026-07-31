from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from afl_model.ratings.elo import expected_home_win_probability


@dataclass(frozen=True)
class PredictionConfig:
    form_elo_scale: float
    rest_elo_scale_per_day: float
    travel_elo_scale_per_100km: float
    line_rounding: float
    confidence_maturity_games: int


@dataclass(frozen=True)
class TeamPredictionInputs:
    elo: float
    attack: float
    defence: float
    form: float
    rest_days: Optional[float]  # signed days vs. baseline; None if unknown (team's first match)
    travel_km: Optional[float]  # None if either the team's or venue's coordinates are unknown
    games_played: int  # how many matches the ratings engine has processed for this team so far


@dataclass(frozen=True)
class PredictionOutput:
    home_win_probability: float
    predicted_margin: float
    predicted_line: float
    predicted_total: float
    predicted_winner: str  # "home" or "away"
    confidence: float  # 0-100


def compute_prediction(
    home: TeamPredictionInputs, away: TeamPredictionInputs,
    home_ground_advantage: float, league_avg_score: float, config: PredictionConfig,
) -> PredictionOutput:
    """Combine Stage 4's rating signals into a single prediction.

    Win probability is Elo-based, with form/rest/travel folded in as
    Elo-equivalent adjustments to the effective rating difference — the
    same mechanism home_ground_advantage already uses. Margin and total
    come purely from attack/defence ratings (points-space); situational
    adjustments don't extend there yet in v1 (see config.yaml).
    """
    situational_elo = (
        config.form_elo_scale * (home.form - away.form)
        + config.rest_elo_scale_per_day * ((home.rest_days or 0.0) - (away.rest_days or 0.0))
        - config.travel_elo_scale_per_100km * (((home.travel_km or 0.0) - (away.travel_km or 0.0)) / 100.0)
    )
    home_win_probability = expected_home_win_probability(
        home.elo + situational_elo, away.elo, home_ground_advantage
    )

    expected_home_score = league_avg_score + home.attack + away.defence
    expected_away_score = league_avg_score + away.attack + home.defence
    predicted_margin = expected_home_score - expected_away_score
    predicted_total = expected_home_score + expected_away_score
    predicted_line = round(predicted_margin / config.line_rounding) * config.line_rounding
    # The winner call must track home_win_probability, not predicted_margin's
    # sign — they come from two independently-fit signals (Elo/situational
    # vs. attack/defence) that are allowed to diverge slightly, but a single
    # prediction can't coherently say both "away is more likely to win" and
    # "we predict home wins." Probability is the one asked to answer that
    # question; margin is a separate points-differential estimate.
    predicted_winner = "home" if home_win_probability >= 0.5 else "away"

    # Two independent, measurable ingredients — never an arbitrary number:
    # how far from a coin flip the win probability is, and how much real
    # history backs the ratings driving it. A confident-looking 80% win
    # probability built from two teams' starting (unraced) ratings isn't
    # actually confident, and this discounts it accordingly.
    prob_confidence = abs(home_win_probability - 0.5) * 2.0
    maturity = min(1.0, min(home.games_played, away.games_played) / config.confidence_maturity_games)
    confidence = prob_confidence * maturity * 100.0

    return PredictionOutput(
        home_win_probability=home_win_probability,
        predicted_margin=predicted_margin,
        predicted_line=predicted_line,
        predicted_total=predicted_total,
        predicted_winner=predicted_winner,
        confidence=confidence,
    )
