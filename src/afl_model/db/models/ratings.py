from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
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

    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
