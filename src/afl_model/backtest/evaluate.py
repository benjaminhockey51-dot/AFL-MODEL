from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from afl_model.backtest.dataset import BacktestMatch, load_backtest_dataset, load_home_ground_advantage
from afl_model.backtest.metrics import (
    CalibrationBucket,
    GroupAccuracy,
    brier_score,
    calibration_table,
    grouped_accuracy,
    log_loss,
    margin_mae,
    win_accuracy,
)
from afl_model.db.models import ModelVersion
from afl_model.models.config import load_prediction_config
from afl_model.models.prediction_math import PredictionConfig, TeamPredictionInputs, compute_prediction

FEATURE_NAMES = ["elo", "attack", "defence", "form", "rest", "travel"]
BY_VENUE_MIN_N = 20


@dataclass(frozen=True)
class VariantSummary:
    name: str
    n: int
    n_decisive: int
    win_accuracy: float
    margin_mae: float
    brier: float
    log_loss: float


@dataclass(frozen=True)
class FullBacktestReport:
    model_version_name: str
    full_model: VariantSummary
    ablations: List[VariantSummary]
    baselines: List[VariantSummary]
    calibration: List[CalibrationBucket]
    by_season: List[GroupAccuracy]
    by_venue: List[GroupAccuracy]
    by_predicted_side: List[GroupAccuracy]
    by_favourite_strength: List[GroupAccuracy]


def _actual_outcome(margin: float) -> float:
    if margin > 0:
        return 1.0
    if margin < 0:
        return 0.0
    return 0.5


def _ablate(inputs: TeamPredictionInputs, flags: Dict[str, bool], neutral_elo: float) -> TeamPredictionInputs:
    """Neutralizes a feature by removing its *contribution*, not by zeroing
    a field that isn't centred at zero: Elo is centred around a starting
    rating (~1500), so "no Elo" means giving both teams the same constant
    (their difference — all that matters to the formula — becomes zero),
    while attack/defence/form are already centred at 0 and rest/travel
    simply become "no adjustment" (None).
    """
    return replace(
        inputs,
        elo=inputs.elo if flags.get("elo", True) else neutral_elo,
        attack=inputs.attack if flags.get("attack", True) else 0.0,
        defence=inputs.defence if flags.get("defence", True) else 0.0,
        form=inputs.form if flags.get("form", True) else 0.0,
        rest_days=inputs.rest_days if flags.get("rest", True) else None,
        travel_km=inputs.travel_km if flags.get("travel", True) else None,
    )


def compute_variant(
    dataset: List[BacktestMatch], home_ground_advantage: float, prediction_config: PredictionConfig,
    flags: Dict[str, bool], name: str, neutral_elo: float = 1500.0,
) -> "tuple[VariantSummary, List[Dict[str, Any]]]":
    """Computes predictions for every match under one feature configuration
    (`flags` maps feature name -> enabled; omitted = enabled). Returns the
    summary metrics plus the per-match computed rows, so the caller can
    build further breakdowns (calibration, by-season, etc.) without
    recomputing predictions.
    """
    computed_rows = []
    for row in dataset:
        home_inputs = _ablate(row.home, flags, neutral_elo)
        away_inputs = _ablate(row.away, flags, neutral_elo)
        output = compute_prediction(home_inputs, away_inputs, home_ground_advantage, row.league_avg_score, prediction_config)
        actual_margin = float(row.home_points - row.away_points)
        computed_rows.append({
            "row": row,
            "output": output,
            "actual_margin": actual_margin,
            "actual_outcome": _actual_outcome(actual_margin),
            "predicted_home_win": output.predicted_winner == "home",
        })

    accuracy, n_decisive = win_accuracy(
        [r["predicted_home_win"] for r in computed_rows], [r["actual_outcome"] for r in computed_rows],
    )
    summary = VariantSummary(
        name=name, n=len(dataset), n_decisive=n_decisive, win_accuracy=accuracy,
        margin_mae=margin_mae([r["output"].predicted_margin for r in computed_rows], [r["actual_margin"] for r in computed_rows]),
        brier=brier_score([r["output"].home_win_probability for r in computed_rows], [r["actual_outcome"] for r in computed_rows]),
        log_loss=log_loss([r["output"].home_win_probability for r in computed_rows], [r["actual_outcome"] for r in computed_rows]),
    )
    return summary, computed_rows


def always_home_baseline(dataset: List[BacktestMatch]) -> VariantSummary:
    """Predict the home team every time, with no margin or probability
    model at all — margin is predicted as a flat 0, and win probability is
    fixed at this same dataset's empirical home win rate (a legitimate,
    simple baseline, but note it's an in-sample rate here, not something a
    walk-forward process arrived at — unlike every other variant in this
    report, which are all genuinely no-lookahead).
    """
    actual_margins = [float(row.home_points - row.away_points) for row in dataset]
    actual_outcomes = [_actual_outcome(m) for m in actual_margins]
    predicted_home_win = [True] * len(dataset)

    accuracy, n_decisive = win_accuracy(predicted_home_win, actual_outcomes)
    home_win_rate = accuracy  # by construction: fraction of decisive matches predicted (=home) correctly
    predicted_probs = [home_win_rate] * len(dataset)
    predicted_margins = [0.0] * len(dataset)

    return VariantSummary(
        name="Always pick home (baseline)", n=len(dataset), n_decisive=n_decisive, win_accuracy=accuracy,
        margin_mae=margin_mae(predicted_margins, actual_margins),
        brier=brier_score(predicted_probs, actual_outcomes),
        log_loss=log_loss(predicted_probs, actual_outcomes),
    )


def _by_season(rows: List[Dict[str, Any]]) -> List[GroupAccuracy]:
    return grouped_accuracy(
        rows,
        group_key=lambda r: str(r["row"].season_year),
        predicted_home_win=lambda r: r["predicted_home_win"],
        actual_outcome=lambda r: r["actual_outcome"],
        predicted_margin=lambda r: r["output"].predicted_margin,
        actual_margin=lambda r: r["actual_margin"],
    )


def _by_venue(rows: List[Dict[str, Any]], min_n: int) -> List[GroupAccuracy]:
    return grouped_accuracy(
        rows,
        group_key=lambda r: r["row"].venue_name or "Unknown venue",
        predicted_home_win=lambda r: r["predicted_home_win"],
        actual_outcome=lambda r: r["actual_outcome"],
        predicted_margin=lambda r: r["output"].predicted_margin,
        actual_margin=lambda r: r["actual_margin"],
        min_n=min_n,
    )


def _by_predicted_side(rows: List[Dict[str, Any]]) -> List[GroupAccuracy]:
    """Accuracy split by which side the model favoured — reveals a
    systematic home/away bias in the model's own picks, distinct from the
    raw home-vs-away win rate in the underlying data.
    """
    return grouped_accuracy(
        rows,
        group_key=lambda r: "Predicted home to win" if r["predicted_home_win"] else "Predicted away to win",
        predicted_home_win=lambda r: r["predicted_home_win"],
        actual_outcome=lambda r: r["actual_outcome"],
        predicted_margin=lambda r: r["output"].predicted_margin,
        actual_margin=lambda r: r["actual_margin"],
    )


def _by_favourite_strength(rows: List[Dict[str, Any]]) -> List[GroupAccuracy]:
    """Accuracy stratified by how strongly the model favoured its pick —
    distinct from the calibration table (which compares predicted
    probability to actual rate); this asks "are we more often simply
    *right* on our confident picks than on close calls."
    """
    def tier(r: Dict[str, Any]) -> str:
        prob = r["output"].home_win_probability
        favoured_prob = prob if r["predicted_home_win"] else 1 - prob
        if favoured_prob >= 0.70:
            return "1. Strong favourite (>=70%)"
        if favoured_prob >= 0.60:
            return "2. Moderate favourite (60-70%)"
        return "3. Close game (50-60%)"

    return grouped_accuracy(
        rows, group_key=tier,
        predicted_home_win=lambda r: r["predicted_home_win"],
        actual_outcome=lambda r: r["actual_outcome"],
        predicted_margin=lambda r: r["output"].predicted_margin,
        actual_margin=lambda r: r["actual_margin"],
    )


def run_full_backtest(
    session: Session, model_version_id: int, prediction_config: Optional[PredictionConfig] = None,
) -> FullBacktestReport:
    model_version = session.get(ModelVersion, model_version_id)
    if model_version is None:
        raise ValueError(f"No model version with id={model_version_id}.")

    home_ground_advantage = load_home_ground_advantage(model_version)
    dataset = load_backtest_dataset(session, model_version_id)
    if not dataset:
        raise ValueError(f"No completed matches with rating history for model version '{model_version.name}'.")

    prediction_config = prediction_config or load_prediction_config()

    full_summary, full_rows = compute_variant(dataset, home_ground_advantage, prediction_config, {}, name="Full model")

    ablations = []
    for feature in FEATURE_NAMES:
        summary, _ = compute_variant(
            dataset, home_ground_advantage, prediction_config, {feature: False},
            name=f"Full model minus {feature}",
        )
        ablations.append(summary)

    elo_only_summary, _ = compute_variant(
        dataset, home_ground_advantage, prediction_config,
        {"form": False, "rest": False, "travel": False},
        name="Elo only (no form/rest/travel)",
    )
    baselines = [elo_only_summary, always_home_baseline(dataset)]

    return FullBacktestReport(
        model_version_name=model_version.name,
        full_model=full_summary,
        ablations=ablations,
        baselines=baselines,
        calibration=calibration_table(
            [r["output"].home_win_probability for r in full_rows], [r["actual_outcome"] for r in full_rows],
        ),
        by_season=_by_season(full_rows),
        by_venue=_by_venue(full_rows, BY_VENUE_MIN_N),
        by_predicted_side=_by_predicted_side(full_rows),
        by_favourite_strength=_by_favourite_strength(full_rows),
    )
