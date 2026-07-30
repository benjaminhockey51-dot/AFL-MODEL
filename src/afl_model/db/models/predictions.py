from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afl_model.db.base import Base


class Prediction(Base):
    """Our own prediction for a match, generated from ratings state alone.

    This table has no relationship to Odds in the ingestion code path —
    predictions are always written before odds are consulted, per the
    project's core rule that bookmaker information must never influence
    the prediction itself. That separation is enforced by the pipeline
    (odds ingestion and prediction generation are independent stages that
    never share input state), not by a database constraint.
    """

    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("match_id", "model_version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))

    predicted_winner_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    predicted_margin: Mapped[float]
    predicted_line: Mapped[float]  # from the home team's perspective
    predicted_total: Mapped[float]
    home_win_probability: Mapped[float]
    confidence: Mapped[float]

    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Odds(Base):
    """Bookmaker odds for a match, captured independently of our prediction.
    snapshot_type distinguishes opening lines from the closing line used as
    the ROI benchmark in backtesting.
    """

    __tablename__ = "odds"
    __table_args__ = (UniqueConstraint("match_id", "bookmaker", "snapshot_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))

    bookmaker: Mapped[str]
    snapshot_type: Mapped[str]  # "open" | "mid" | "close"

    home_decimal_odds: Mapped[Optional[float]] = mapped_column(default=None)
    away_decimal_odds: Mapped[Optional[float]] = mapped_column(default=None)
    home_line: Mapped[Optional[float]] = mapped_column(default=None)
    away_line: Mapped[Optional[float]] = mapped_column(default=None)
    total_line: Mapped[Optional[float]] = mapped_column(default=None)

    captured_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source: Mapped[str]


class PredictionResult(Base):
    """Post-match reconciliation of a prediction against the actual result
    and the closing line — the permanent record behind performance
    reporting (Stage 8).
    """

    __tablename__ = "prediction_results"
    __table_args__ = (UniqueConstraint("prediction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))

    winner_correct: Mapped[Optional[bool]] = mapped_column(Boolean, default=None)
    margin_error: Mapped[Optional[float]] = mapped_column(default=None)
    total_error: Mapped[Optional[float]] = mapped_column(default=None)
    closing_line_diff: Mapped[Optional[float]] = mapped_column(default=None)

    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
