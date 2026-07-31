from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.db.models import Match, MatchSourceRef

logger = logging.getLogger(__name__)


class MatchUpsertOutcome(Enum):
    CREATED = "created"  # a brand new Match row
    LINKED_EXISTING = "linked_existing"  # another source already created this match
    RESYNCED = "resynced"  # already synced from this source before; fields refreshed


@dataclass
class NaturalKey:
    season_year: int
    match_date: date
    home_team_id: int
    away_team_id: int


def upsert_match(
    session: Session,
    source: str,
    source_match_id: str,
    natural_key: NaturalKey,
    fields: Dict[str, Any],
) -> "tuple[Match, MatchUpsertOutcome]":
    """Find-or-create the canonical Match for one source's view of a game,
    and record that source's external ID against it.

    Two different sources describing the same real-world game must resolve
    to the *same* Match row — this is the shared reconciliation path both
    afl_model.data.ingest_squiggle and afl_model.data.ingest_afltables use,
    so there is exactly one place that decides what "the same match" means.
    """
    ref = session.execute(
        sa.select(MatchSourceRef).where(
            MatchSourceRef.source == source, MatchSourceRef.source_match_id == source_match_id
        )
    ).scalar_one_or_none()

    if ref is not None:
        match = session.get(Match, ref.match_id)
        for key, value in fields.items():
            setattr(match, key, value)
        ref.last_synced_at = datetime.utcnow()
        return match, MatchUpsertOutcome.RESYNCED

    match = session.execute(
        sa.select(Match).where(
            Match.season_year == natural_key.season_year,
            Match.match_date == natural_key.match_date,
            Match.home_team_id == natural_key.home_team_id,
            Match.away_team_id == natural_key.away_team_id,
        )
    ).scalar_one_or_none()

    outcome = MatchUpsertOutcome.LINKED_EXISTING
    if match is None:
        match = Match(
            created_by_source=source,
            created_by_source_match_id=source_match_id,
            season_year=natural_key.season_year,
            match_date=natural_key.match_date,
            home_team_id=natural_key.home_team_id,
            away_team_id=natural_key.away_team_id,
            **fields,
        )
        session.add(match)
        session.flush()
        outcome = MatchUpsertOutcome.CREATED
    else:
        for key, value in fields.items():
            setattr(match, key, value)

    session.add(MatchSourceRef(match_id=match.id, source=source, source_match_id=source_match_id))
    return match, outcome
