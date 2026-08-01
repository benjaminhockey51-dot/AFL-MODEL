from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Match, Odds, Prediction, PredictionResult


@dataclass
class ReconcileSummary:
    predictions_checked: int = 0
    created: int = 0
    updated: int = 0
    with_closing_odds: int = 0


def _actual_winner_team_id(match: Match) -> Optional[int]:
    margin = match.home_points - match.away_points
    if margin > 0:
        return match.home_team_id
    if margin < 0:
        return match.away_team_id
    return None  # a genuine draw — no "correct" winner call is possible


def reconcile_predictions() -> ReconcileSummary:
    """Compares every stored Prediction whose match now has a final result
    against that result, upserting a PredictionResult row per prediction.
    This is what lets the software "always know how accurate it has been" —
    without it, Predictions accumulate but nothing ever checks them against
    reality.

    Safe to re-run at any time (e.g. after each round completes): already-
    reconciled predictions are refreshed in place, not duplicated.
    """
    summary = ReconcileSummary()
    session = get_session()
    try:
        predictions = session.execute(
            sa.select(Prediction).join(Match, Prediction.match_id == Match.id)
            .where(Match.home_points.is_not(None), Match.away_points.is_not(None))
        ).scalars().all()
        summary.predictions_checked = len(predictions)

        for prediction in predictions:
            match = session.get(Match, prediction.match_id)
            actual_margin = match.home_points - match.away_points
            actual_total = match.home_points + match.away_points
            actual_winner_id = _actual_winner_team_id(match)

            winner_correct = (
                None if actual_winner_id is None
                else prediction.predicted_winner_team_id == actual_winner_id
            )

            closing_odds = session.execute(
                sa.select(Odds).where(Odds.match_id == match.id, Odds.snapshot_type == "close")
            ).scalars().first()
            closing_line_diff = None
            if closing_odds is not None and closing_odds.home_line is not None:
                closing_line_diff = prediction.predicted_line - closing_odds.home_line
                summary.with_closing_odds += 1

            fields = dict(
                winner_correct=winner_correct,
                margin_error=prediction.predicted_margin - actual_margin,
                total_error=prediction.predicted_total - actual_total,
                closing_line_diff=closing_line_diff,
            )

            existing = session.execute(
                sa.select(PredictionResult).where(PredictionResult.prediction_id == prediction.id)
            ).scalar_one_or_none()
            if existing is None:
                session.add(PredictionResult(prediction_id=prediction.id, **fields))
                summary.created += 1
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
                summary.updated += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return summary
