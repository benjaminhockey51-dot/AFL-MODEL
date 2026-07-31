from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, replace
from typing import Dict, List

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.backtest.metrics import brier_score, log_loss, margin_mae, win_accuracy
from afl_model.db.models import Match, Team, Venue
from afl_model.models.config import load_prediction_config
from afl_model.models.prediction_math import PredictionConfig, compute_prediction
from afl_model.ratings.config import RatingsConfig, load_ratings_config
from afl_model.ratings.engine import MatchWalkForwardSnapshot, compute_walk_forward
from afl_model.tuning import grid
from afl_model.tuning.split import TEST_SEASONS, VALIDATION_SEASONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WinProbabilityCandidate:
    k_factor: float
    home_ground_advantage: float
    season_regression_factor: float
    form_elo_scale: float
    rest_elo_scale_per_day: float
    travel_elo_scale_per_100km: float
    log_loss: float
    brier: float
    accuracy: float
    n: int


@dataclass(frozen=True)
class MarginCandidate:
    attack_defence_k_factor: float
    league_avg_score_ewma_alpha: float
    margin_mae: float
    n: int


@dataclass(frozen=True)
class SearchResult:
    best_win_probability: WinProbabilityCandidate
    best_margin: MarginCandidate
    win_probability_candidates: List[WinProbabilityCandidate]
    margin_candidates: List[MarginCandidate]
    frozen_ratings_config: RatingsConfig
    frozen_prediction_config: PredictionConfig
    validation_win_probability: Dict[str, float]
    validation_margin: Dict[str, float]
    test_win_probability: Dict[str, float]
    test_margin: Dict[str, float]


def _score_win_probability(
    snapshots: List[MatchWalkForwardSnapshot], home_ground_advantage: float, prediction_config: PredictionConfig,
) -> Dict[str, float]:
    predicted_probs = []
    actual_outcomes = []
    predicted_home_win = []
    for snap in snapshots:
        output = compute_prediction(
            snap.home, snap.away, home_ground_advantage, snap.league_avg_score_before, prediction_config,
        )
        predicted_probs.append(output.home_win_probability)
        predicted_home_win.append(output.predicted_winner == "home")
        margin = snap.match.home_points - snap.match.away_points
        actual_outcomes.append(1.0 if margin > 0 else (0.0 if margin < 0 else 0.5))

    accuracy, n_decisive = win_accuracy(predicted_home_win, actual_outcomes)
    return {
        "log_loss": log_loss(predicted_probs, actual_outcomes),
        "brier": brier_score(predicted_probs, actual_outcomes),
        "accuracy": accuracy,
        "n": float(len(snapshots)),
        "n_decisive": float(n_decisive),
    }


def _score_margin(snapshots: List[MatchWalkForwardSnapshot], prediction_config: PredictionConfig) -> Dict[str, float]:
    predicted_margins = []
    actual_margins = []
    for snap in snapshots:
        # home_ground_advantage doesn't affect margin/total in this
        # architecture — 0.0 keeps this call unambiguous either way.
        output = compute_prediction(snap.home, snap.away, 0.0, snap.league_avg_score_before, prediction_config)
        predicted_margins.append(output.predicted_margin)
        actual_margins.append(float(snap.match.home_points - snap.match.away_points))
    return {"margin_mae": margin_mae(predicted_margins, actual_margins), "n": float(len(snapshots))}


def _validation_snapshots(snapshots: List[MatchWalkForwardSnapshot]) -> List[MatchWalkForwardSnapshot]:
    return [s for s in snapshots if s.match.season_year in VALIDATION_SEASONS]


def _test_snapshots(snapshots: List[MatchWalkForwardSnapshot]) -> List[MatchWalkForwardSnapshot]:
    return [s for s in snapshots if s.match.season_year in TEST_SEASONS]


def search_win_probability_params(
    matches, teams_by_id, venues_by_id, base_ratings_config: RatingsConfig, base_prediction_config: PredictionConfig,
) -> List[WinProbabilityCandidate]:
    """Stage A: grid search over the six hyperparameters feeding win
    probability, scored by log loss on the validation seasons only (see
    afl_model.tuning.split — the test seasons are never touched here).

    The outer grid (k_factor, home ground advantage, season regression
    factor) changes the walk-forward Elo trajectory itself, so each
    combination needs its own recomputation. The inner grid (form, rest,
    travel) only affects the prediction-time combination, never a stored
    rating, so every inner combination reuses the same walk-forward result
    — this is what keeps a ~45,000-combination search tractable without
    approximating anything.
    """
    candidates = []
    outer_grid = list(itertools.product(
        grid.ELO_K_FACTOR_GRID, grid.HOME_GROUND_ADVANTAGE_GRID, grid.SEASON_REGRESSION_FACTOR_GRID,
    ))
    logger.info("Stage A: %d outer combinations, each with %d inner combinations",
                len(outer_grid),
                len(grid.FORM_ELO_SCALE_GRID) * len(grid.REST_ELO_SCALE_PER_DAY_GRID) * len(grid.TRAVEL_ELO_SCALE_PER_100KM_GRID))

    for i, (k_factor, hga, regression_factor) in enumerate(outer_grid):
        elo_config = replace(
            base_ratings_config.elo, k_factor=k_factor, home_ground_advantage=hga,
            season_regression_factor=regression_factor,
        )
        ratings_config = replace(base_ratings_config, elo=elo_config)
        result = compute_walk_forward(matches, teams_by_id, venues_by_id, ratings_config)
        validation = _validation_snapshots(result.snapshots)

        for form_scale, rest_scale, travel_scale in itertools.product(
            grid.FORM_ELO_SCALE_GRID, grid.REST_ELO_SCALE_PER_DAY_GRID, grid.TRAVEL_ELO_SCALE_PER_100KM_GRID,
        ):
            prediction_config = replace(
                base_prediction_config, form_elo_scale=form_scale,
                rest_elo_scale_per_day=rest_scale, travel_elo_scale_per_100km=travel_scale,
            )
            scores = _score_win_probability(validation, hga, prediction_config)
            candidates.append(WinProbabilityCandidate(
                k_factor=k_factor, home_ground_advantage=hga, season_regression_factor=regression_factor,
                form_elo_scale=form_scale, rest_elo_scale_per_day=rest_scale,
                travel_elo_scale_per_100km=travel_scale,
                log_loss=scores["log_loss"], brier=scores["brier"], accuracy=scores["accuracy"],
                n=int(scores["n"]),
            ))
        if (i + 1) % 50 == 0:
            logger.info("Stage A: %d/%d outer combinations done", i + 1, len(outer_grid))

    return candidates


def search_margin_params(
    matches, teams_by_id, venues_by_id, base_ratings_config: RatingsConfig, base_prediction_config: PredictionConfig,
) -> List[MarginCandidate]:
    """Stage B: grid search over the two attack/defence hyperparameters,
    scored by margin MAE on the validation seasons. Independent of Stage
    A — margin/total never depend on Elo or situational adjustments in
    this architecture (confirmed by Stage 5's leave-one-out ablation).
    """
    candidates = []
    for k_factor, alpha in itertools.product(
        grid.ATTACK_DEFENCE_K_FACTOR_GRID, grid.LEAGUE_AVG_SCORE_EWMA_ALPHA_GRID,
    ):
        ad_config = replace(base_ratings_config.attack_defence, k_factor=k_factor, league_avg_score_ewma_alpha=alpha)
        ratings_config = replace(base_ratings_config, attack_defence=ad_config)
        result = compute_walk_forward(matches, teams_by_id, venues_by_id, ratings_config)
        validation = _validation_snapshots(result.snapshots)
        scores = _score_margin(validation, base_prediction_config)
        candidates.append(MarginCandidate(
            attack_defence_k_factor=k_factor, league_avg_score_ewma_alpha=alpha,
            margin_mae=scores["margin_mae"], n=int(scores["n"]),
        ))
    return candidates


def run_full_search(session: Session) -> SearchResult:
    """Runs both search stages, freezes the winning hyperparameters, and
    evaluates that frozen configuration exactly once on the held-out test
    seasons — the only time they're used for anything in this whole
    process. No parameter is adjusted after seeing that result.
    """
    teams_by_id = {t.id: t for t in session.execute(sa.select(Team)).scalars().all()}
    venues_by_id = {v.id: v for v in session.execute(sa.select(Venue)).scalars().all()}
    matches = session.execute(
        sa.select(Match).where(Match.home_points.is_not(None), Match.away_points.is_not(None))
        .order_by(Match.match_date, Match.match_datetime)
    ).scalars().all()

    base_ratings_config = load_ratings_config()
    base_prediction_config = load_prediction_config()

    logger.info("Running Stage A (win probability) grid search...")
    win_prob_candidates = search_win_probability_params(
        matches, teams_by_id, venues_by_id, base_ratings_config, base_prediction_config,
    )
    best_win_prob = min(win_prob_candidates, key=lambda c: c.log_loss)

    logger.info("Running Stage B (margin) grid search...")
    margin_candidates = search_margin_params(
        matches, teams_by_id, venues_by_id, base_ratings_config, base_prediction_config,
    )
    best_margin = min(margin_candidates, key=lambda c: c.margin_mae)

    frozen_elo = replace(
        base_ratings_config.elo, k_factor=best_win_prob.k_factor,
        home_ground_advantage=best_win_prob.home_ground_advantage,
        season_regression_factor=best_win_prob.season_regression_factor,
    )
    frozen_ad = replace(
        base_ratings_config.attack_defence, k_factor=best_margin.attack_defence_k_factor,
        league_avg_score_ewma_alpha=best_margin.league_avg_score_ewma_alpha,
    )
    frozen_ratings_config = replace(base_ratings_config, elo=frozen_elo, attack_defence=frozen_ad)
    frozen_prediction_config = replace(
        base_prediction_config, form_elo_scale=best_win_prob.form_elo_scale,
        rest_elo_scale_per_day=best_win_prob.rest_elo_scale_per_day,
        travel_elo_scale_per_100km=best_win_prob.travel_elo_scale_per_100km,
    )

    logger.info("Running final walk-forward with frozen config for validation + held-out test evaluation...")
    final_result = compute_walk_forward(matches, teams_by_id, venues_by_id, frozen_ratings_config)
    validation_snaps = _validation_snapshots(final_result.snapshots)
    test_snaps = _test_snapshots(final_result.snapshots)

    return SearchResult(
        best_win_probability=best_win_prob,
        best_margin=best_margin,
        win_probability_candidates=win_prob_candidates,
        margin_candidates=margin_candidates,
        frozen_ratings_config=frozen_ratings_config,
        frozen_prediction_config=frozen_prediction_config,
        validation_win_probability=_score_win_probability(
            validation_snaps, frozen_ratings_config.elo.home_ground_advantage, frozen_prediction_config,
        ),
        validation_margin=_score_margin(validation_snaps, frozen_prediction_config),
        test_win_probability=_score_win_probability(
            test_snaps, frozen_ratings_config.elo.home_ground_advantage, frozen_prediction_config,
        ),
        test_margin=_score_margin(test_snaps, frozen_prediction_config),
    )
