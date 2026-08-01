from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Match, Prediction, PredictionResult


@dataclass(frozen=True)
class SeasonPerformance:
    season_year: int
    n: int
    n_decisive: int
    win_accuracy: Optional[float]
    margin_mae: Optional[float]
    total_mae: Optional[float]


@dataclass(frozen=True)
class PerformanceReport:
    overall_n: int
    overall_n_decisive: int
    overall_win_accuracy: Optional[float]
    overall_margin_mae: Optional[float]
    overall_total_mae: Optional[float]
    n_with_closing_odds: int
    mean_closing_line_diff: Optional[float]
    by_season: List[SeasonPerformance]
    recent_n: int
    recent_win_accuracy: Optional[float]
    recent_margin_mae: Optional[float]


def _win_accuracy(results: List[PredictionResult]) -> "tuple[Optional[float], int]":
    decisive = [r for r in results if r.winner_correct is not None]
    if not decisive:
        return None, 0
    return sum(1 for r in decisive if r.winner_correct) / len(decisive), len(decisive)


def _mean_abs(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(abs(v) for v in values) / len(values)


def build_performance_report(recent_n: int = 20) -> PerformanceReport:
    """Every real, reconciled prediction this model has ever made — this is
    how the software "always knows how accurate it has been." Built
    entirely from PredictionResult (afl_model.reporting.reconcile), so a
    prediction only counts once its match has actually been played and
    reconciled against reality — nothing here is a backtest or a forecast.
    """
    session = get_session()
    try:
        rows = session.execute(
            sa.select(PredictionResult, Match)
            .join(Prediction, PredictionResult.prediction_id == Prediction.id)
            .join(Match, Prediction.match_id == Match.id)
            .order_by(Match.match_date.desc())
        ).all()

        results = [r[0] for r in rows]
        matches = [r[1] for r in rows]

        overall_accuracy, overall_decisive = _win_accuracy(results)
        overall_margin_mae = _mean_abs([r.margin_error for r in results])
        overall_total_mae = _mean_abs([r.total_error for r in results])

        with_closing = [r.closing_line_diff for r in results if r.closing_line_diff is not None]
        mean_closing_diff = sum(with_closing) / len(with_closing) if with_closing else None

        by_season_groups = {}
        for result, match in zip(results, matches):
            by_season_groups.setdefault(match.season_year, []).append(result)
        by_season = []
        for season_year, season_results in sorted(by_season_groups.items()):
            acc, n_decisive = _win_accuracy(season_results)
            by_season.append(SeasonPerformance(
                season_year=season_year, n=len(season_results), n_decisive=n_decisive,
                win_accuracy=acc, margin_mae=_mean_abs([r.margin_error for r in season_results]),
                total_mae=_mean_abs([r.total_error for r in season_results]),
            ))

        # rows are already ordered most-recent-match-first.
        recent = results[:recent_n]
        recent_accuracy, _ = _win_accuracy(recent)
        recent_margin_mae = _mean_abs([r.margin_error for r in recent])

        return PerformanceReport(
            overall_n=len(results), overall_n_decisive=overall_decisive,
            overall_win_accuracy=overall_accuracy, overall_margin_mae=overall_margin_mae,
            overall_total_mae=overall_total_mae, n_with_closing_odds=len(with_closing),
            mean_closing_line_diff=mean_closing_diff, by_season=by_season,
            recent_n=len(recent), recent_win_accuracy=recent_accuracy, recent_margin_mae=recent_margin_mae,
        )
    finally:
        session.close()
