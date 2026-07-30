from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from afl_model.db.base import Base


class Match(Base):
    """One row per AFL match (home & away, minor round or final).

    (source, source_match_id) makes ingestion idempotent — re-running a
    scrape upserts rather than duplicates. Scores are stored as
    goals/behinds, not just total points, since points is a derived value
    (goals * 6 + behinds) and goals/behinds are independently meaningful.
    """

    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("source", "source_match_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    season_year: Mapped[int] = mapped_column(ForeignKey("seasons.year"))
    round_number: Mapped[int]
    round_name: Mapped[str]
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)

    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), default=None)
    match_date: Mapped[date] = mapped_column(Date)
    match_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    home_goals: Mapped[Optional[int]] = mapped_column(default=None)
    home_behinds: Mapped[Optional[int]] = mapped_column(default=None)
    home_points: Mapped[Optional[int]] = mapped_column(default=None)
    away_goals: Mapped[Optional[int]] = mapped_column(default=None)
    away_behinds: Mapped[Optional[int]] = mapped_column(default=None)
    away_points: Mapped[Optional[int]] = mapped_column(default=None)

    attendance: Mapped[Optional[int]] = mapped_column(default=None)
    weather_temp_c: Mapped[Optional[float]] = mapped_column(default=None)
    weather_condition: Mapped[Optional[str]] = mapped_column(default=None)

    source: Mapped[str]
    source_match_id: Mapped[str]


class TeamMatchStats(Base):
    """Team-level box-score stats for one team in one match.

    Columns are nullable throughout: not every stat is available for every
    match, and this table must degrade gracefully rather than block
    ingestion when a source is missing a field.
    """

    __tablename__ = "team_match_stats"
    __table_args__ = (UniqueConstraint("match_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    inside_50s: Mapped[Optional[int]] = mapped_column(default=None)
    contested_possessions: Mapped[Optional[int]] = mapped_column(default=None)
    uncontested_possessions: Mapped[Optional[int]] = mapped_column(default=None)
    contested_marks: Mapped[Optional[int]] = mapped_column(default=None)
    marks: Mapped[Optional[int]] = mapped_column(default=None)
    clearances: Mapped[Optional[int]] = mapped_column(default=None)
    hitouts: Mapped[Optional[int]] = mapped_column(default=None)
    tackles: Mapped[Optional[int]] = mapped_column(default=None)
    disposals: Mapped[Optional[int]] = mapped_column(default=None)
    kicks: Mapped[Optional[int]] = mapped_column(default=None)
    handballs: Mapped[Optional[int]] = mapped_column(default=None)
    frees_for: Mapped[Optional[int]] = mapped_column(default=None)
    frees_against: Mapped[Optional[int]] = mapped_column(default=None)
    turnovers: Mapped[Optional[int]] = mapped_column(default=None)
    intercepts: Mapped[Optional[int]] = mapped_column(default=None)
    metres_gained: Mapped[Optional[int]] = mapped_column(default=None)


class PlayerMatchStats(Base):
    """Player-level box-score stats for one player in one match."""

    __tablename__ = "player_match_stats"
    __table_args__ = (UniqueConstraint("match_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))

    disposals: Mapped[Optional[int]] = mapped_column(default=None)
    kicks: Mapped[Optional[int]] = mapped_column(default=None)
    handballs: Mapped[Optional[int]] = mapped_column(default=None)
    marks: Mapped[Optional[int]] = mapped_column(default=None)
    tackles: Mapped[Optional[int]] = mapped_column(default=None)
    goals: Mapped[Optional[int]] = mapped_column(default=None)
    behinds: Mapped[Optional[int]] = mapped_column(default=None)
    hitouts: Mapped[Optional[int]] = mapped_column(default=None)
    clearances: Mapped[Optional[int]] = mapped_column(default=None)
    contested_possessions: Mapped[Optional[int]] = mapped_column(default=None)
    uncontested_possessions: Mapped[Optional[int]] = mapped_column(default=None)
    time_on_ground_pct: Mapped[Optional[float]] = mapped_column(default=None)


class TeamSelection(Base):
    """Named/played status for a player in a given match — the raw input to
    the injury/availability rating adjustment (Stage 4). Recorded even for
    players who were selected but did not end up with recorded stats
    (e.g. late outs), which is exactly the signal that adjustment needs.
    """

    __tablename__ = "team_selections"
    __table_args__ = (UniqueConstraint("match_id", "team_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))

    was_named: Mapped[bool] = mapped_column(Boolean, default=True)
    was_played: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[Optional[str]] = mapped_column(default=None)
