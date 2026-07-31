from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afl_model.db.base import Base


class ModelVersion(Base):
    """Identifies the exact rating/prediction configuration that produced a
    given row of history. Config changes over years of tuning — without
    this, historical ratings/predictions can't be reproduced or attributed,
    which breaks the backtesting discipline of comparing versions honestly.
    """

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    config_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notes: Mapped[Optional[str]] = mapped_column(default=None)

    # The attack/defence engine's league-average score is global run state,
    # not per-team — recorded here (once, at the end of the run) so the
    # prediction engine can use the same value a fresh walk-forward pass
    # would have arrived at, without re-walking all of history to get it.
    final_league_avg_score: Mapped[Optional[float]] = mapped_column(default=None)


class TeamRatingHistory(Base):
    """Append-only snapshot of a team's ratings as they stood immediately
    before a given match. Never overwritten or recomputed retroactively —
    this is what makes walk-forward backtesting (Stage 7) possible: the
    model's belief on any past date is always reconstructable exactly.
    """

    __tablename__ = "team_rating_history"
    __table_args__ = (UniqueConstraint("match_id", "team_id", "model_version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))

    elo_rating: Mapped[Optional[float]] = mapped_column(default=None)
    attack_rating: Mapped[Optional[float]] = mapped_column(default=None)
    defence_rating: Mapped[Optional[float]] = mapped_column(default=None)
    form_rating: Mapped[Optional[float]] = mapped_column(default=None)
    travel_adjustment: Mapped[Optional[float]] = mapped_column(default=None)
    rest_adjustment: Mapped[Optional[float]] = mapped_column(default=None)
    injury_adjustment: Mapped[Optional[float]] = mapped_column(default=None)
    # The league-average score used to compute this match's expected scores
    # AT THE TIME — not the run's final value. league_avg_score is a slow
    # EWMA that drifts across eras, so using the final (most recent) value
    # to backtest an early match would leak a later scoring environment
    # into a supposedly no-lookahead evaluation. Duplicated across both
    # teams' rows for the same match (it's match-level, not team-level
    # state) — a small redundancy that avoids a separate table.
    league_avg_score_before: Mapped[Optional[float]] = mapped_column(default=None)

    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CurrentTeamRating(Base):
    """A team's rating state *after* the last match a given ratings run
    processed for them — i.e. what should be used to predict their next,
    not-yet-played fixture. TeamRatingHistory only stores pre-match
    snapshots (deliberately, for no-lookahead backtesting), so it has
    nothing usable for a match that hasn't happened yet; this table is
    that missing "current state," persisted once at the end of a ratings
    run rather than recomputed by re-walking all history on every
    prediction.

    Predicting an *already-played* match (e.g. for backtesting) must use
    TeamRatingHistory's snapshot for that specific match instead of this
    table — using "current" state there would leak future information.
    """

    __tablename__ = "current_team_ratings"
    __table_args__ = (UniqueConstraint("team_id", "model_version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))

    elo_rating: Mapped[float]
    attack_rating: Mapped[float]
    defence_rating: Mapped[float]
    form_rating: Mapped[float]
    last_match_date: Mapped[Optional[date]] = mapped_column(Date, default=None)
    last_season: Mapped[Optional[int]] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
