from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from afl_model.db.base import Base


class Season(Base):
    """One row per AFL season. Year is the natural key."""

    __tablename__ = "seasons"

    year: Mapped[int] = mapped_column(primary_key=True)
    notes: Mapped[Optional[str]] = mapped_column(default=None)


class Team(Base):
    """Canonical club entity. Scope starts at the 2018 season, by which point
    all 18 current AFL clubs (including Gold Coast and GWS) were established
    and stable, so this table does not need to model historical mergers.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    abbreviation: Mapped[str] = mapped_column(unique=True)

    aliases: Mapped[list["TeamAlias"]] = relationship(back_populates="team")


class TeamAlias(Base):
    """Maps a source-specific spelling of a team's name to the canonical team.

    Different sources render the same club differently (e.g. "GWS" vs
    "GWS Giants" vs "Greater Western Sydney") — ingestion code resolves
    through this table rather than string-matching team names directly.
    """

    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("source", "alias_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    source: Mapped[str]
    alias_name: Mapped[str]

    team: Mapped[Team] = relationship(back_populates="aliases")


class Venue(Base):
    """Canonical venue entity. Latitude/longitude support travel-distance
    adjustments in the ratings engine (Stage 4).
    """

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    city: Mapped[Optional[str]] = mapped_column(default=None)
    state: Mapped[Optional[str]] = mapped_column(default=None)
    latitude: Mapped[Optional[float]] = mapped_column(default=None)
    longitude: Mapped[Optional[float]] = mapped_column(default=None)

    aliases: Mapped[list["VenueAlias"]] = relationship(back_populates="venue")


class VenueAlias(Base):
    """Maps a source-specific / historical venue name to the canonical venue.

    Needed even within the 2018+ scope: e.g. Etihad Stadium was renamed
    Marvel Stadium in 2018, right at the project's start boundary.
    """

    __tablename__ = "venue_aliases"
    __table_args__ = (UniqueConstraint("source", "alias_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    source: Mapped[str]
    alias_name: Mapped[str]

    venue: Mapped[Venue] = relationship(back_populates="aliases")


class Player(Base):
    """Canonical player entity, resolved from source-specific player IDs."""

    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("source", "source_player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, default=None)
    source: Mapped[str]
    source_player_id: Mapped[str]
