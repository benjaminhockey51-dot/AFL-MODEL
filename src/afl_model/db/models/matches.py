from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from afl_model.db.base import Base


class Match(Base):
    """One row per AFL match (home & away, minor round or final).

    Identity is the real-world natural key — (season_year, match_date,
    home_team_id, away_team_id) — not any one source's ID. Two teams never
    play each other twice on the same date, so this holds regardless of how
    many sources describe the match, and it sidesteps a real inconsistency
    between sources: Squiggle numbers finals rounds (24/25/26/27) while AFL
    Tables names them ("Qualifying Final", "Elimination Final", ...) — round
    number/name is descriptive here, not identity.

    Per-source external IDs (for idempotent re-syncing from one source) live
    in MatchSourceRef, not on this table, because a single canonical match
    is enriched by multiple independent sources (Squiggle for fixtures/
    live results, AFL Tables for attendance/stats/history). created_by_*
    records only which source first created the row, for provenance.
    """

    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint(
            "season_year", "match_date", "home_team_id", "away_team_id",
            name="uq_matches_season_date_home_away",
        ),
    )

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

    created_by_source: Mapped[str]
    created_by_source_match_id: Mapped[str]


class MatchSourceRef(Base):
    """Links a canonical Match to one source's external ID for that match —
    the idempotency key each source's ingestion pipeline upserts against.
    A match typically has one ref per source that has ever ingested it.
    """

    __tablename__ = "match_source_refs"
    __table_args__ = (UniqueConstraint("source", "source_match_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    source: Mapped[str]
    source_match_id: Mapped[str]
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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

    # The remaining columns match AFL Tables' full per-player stat line
    # exactly (see its "Abbreviations key") — captured now rather than
    # partially, since re-scraping years of history later to backfill a
    # missed column would mean hitting afltables.com all over again.
    inside_50s: Mapped[Optional[int]] = mapped_column(default=None)
    rebound_50s: Mapped[Optional[int]] = mapped_column(default=None)
    clangers: Mapped[Optional[int]] = mapped_column(default=None)
    frees_for: Mapped[Optional[int]] = mapped_column(default=None)
    frees_against: Mapped[Optional[int]] = mapped_column(default=None)
    brownlow_votes: Mapped[Optional[int]] = mapped_column(default=None)
    contested_marks: Mapped[Optional[int]] = mapped_column(default=None)
    marks_inside_50: Mapped[Optional[int]] = mapped_column(default=None)
    one_percenters: Mapped[Optional[int]] = mapped_column(default=None)
    bounces: Mapped[Optional[int]] = mapped_column(default=None)
    goal_assists: Mapped[Optional[int]] = mapped_column(default=None)


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
