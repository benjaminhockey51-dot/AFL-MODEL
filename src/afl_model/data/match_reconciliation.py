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

# Squiggle and AFL Tables can disagree on round numbering (e.g. Squiggle
# adding an "Opening Round" mid-2026 that AFL Tables' own site never
# adopted) — see docs/decisions/0001-round-number-source-precedence.md.
# Squiggle is authoritative for these two fields: it's the only source
# with round data for unplayed/future matches at all, so making it
# authoritative for played matches too just makes the existing asymmetry
# consistent, rather than having whichever ingester ran last silently win.
FIELD_SOURCE_PRECEDENCE: Dict[str, str] = {
    "round_number": "squiggle",
    "round_name": "squiggle",
}


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


def _apply_fields(match: Match, source: str, fields: Dict[str, Any]) -> None:
    """Applies an ingester's fields to an already-existing match, deferring
    to FIELD_SOURCE_PRECEDENCE for fields where sources are known to
    disagree. A non-authoritative source's differing value is logged, not
    silently discarded, so a future disagreement we haven't seen yet stays
    visible instead of quietly winning or losing by ingestion order.
    """
    for key, value in fields.items():
        owner = FIELD_SOURCE_PRECEDENCE.get(key)
        if owner is not None and owner != source:
            current = getattr(match, key)
            if current != value:
                logger.warning(
                    "%s reported %s=%r for match %d (%s, %s v %s) but %s is authoritative "
                    "for this field — keeping %r.",
                    source, key, value, match.id, match.match_date,
                    match.home_team_id, match.away_team_id, owner, current,
                )
            continue
        setattr(match, key, value)


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
        _apply_fields(match, source, fields)
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
        _apply_fields(match, source, fields)

    session.add(MatchSourceRef(match_id=match.id, source=source, source_match_id=source_match_id))
    return match, outcome
