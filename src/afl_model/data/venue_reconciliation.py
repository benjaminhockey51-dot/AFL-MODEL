from __future__ import annotations

import logging
from typing import List, Tuple

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.db.connection import get_session
from afl_model.db.models import Match, Venue, VenueAlias

logger = logging.getLogger(__name__)

# Squiggle names venues inconsistently across seasons (sponsor renames), while
# AFL Tables uses a stable internal slug for the same physical ground
# regardless of sponsor. Each pair below was confirmed by cross-referencing
# a real match that both sources describe (same date/home team) and
# checking they report the identical game at the "different" venue name —
# see the Stage 3 write-up for the specific matches checked. Not guessed.
KNOWN_VENUE_DUPLICATES: List[Tuple[str, str, str]] = [
    # (squiggle alias name, afltables canonical slug, human note)
    ("Marvel Stadium", "docklands", "Docklands Stadium, Melbourne — sponsor rename"),
    ("GMHBA Stadium", "kardinia_park", "Kardinia Park, Geelong — sponsor rename"),
    ("Optus Stadium", "perth", "Perth Stadium — sponsor rename"),
    ("Mars Stadium", "eureka", "Eureka Stadium, Ballarat — sponsor rename"),
    ("Adelaide Arena at Jiangwan Stadium", "jiangwan",
     "Jiangwan Stadium, Shanghai — Port Adelaide's China games (a well-documented "
     "annual AFL fixture). Confirmed indirectly: the Squiggle-only venue row this "
     "alias pointed to had zero matches left referencing it once AFL Tables' "
     "ingestion ran for the same season and resolved the same games to 'jiangwan' "
     "instead — i.e. it's a leftover duplicate of the same real match, not a "
     "distinct venue"),
    ("UNSW Canberra Oval", "manuka_oval", "Manuka Oval, Canberra — sponsor rename; "
     "confirmed via GWS v Western Bulldogs, 2018-03-25"),
    ("University of Tasmania Stadium", "york_park", "York Park, Launceston — sponsor rename; "
     "confirmed via 4 Hawthorn home games in 2018 (NOT Bellerive Oval, which is a separate "
     "Hobart venue — initially assumed otherwise, verified against real data instead)"),
]


def _merge_venue(session: Session, duplicate: Venue, canonical: Venue) -> None:
    if duplicate.id == canonical.id:
        return
    session.execute(
        sa.update(Match).where(Match.venue_id == duplicate.id).values(venue_id=canonical.id)
    )
    session.execute(
        sa.update(VenueAlias).where(VenueAlias.venue_id == duplicate.id).values(venue_id=canonical.id)
    )
    session.delete(duplicate)
    logger.info("Merged duplicate venue '%s' (id=%d) into '%s' (id=%d)",
                duplicate.name, duplicate.id, canonical.name, canonical.id)


def reconcile_known_venue_duplicates() -> int:
    """Merge Squiggle's sponsor-named venue duplicates into their AFL
    Tables canonical equivalent. Safe to re-run at any time — a no-op once
    a pair has already been merged, and a no-op for pairs where one side
    hasn't been ingested yet.
    """
    session = get_session()
    merged = 0
    try:
        for squiggle_alias, afltables_slug, _note in KNOWN_VENUE_DUPLICATES:
            dup_alias = session.execute(
                sa.select(VenueAlias).where(
                    VenueAlias.source == "squiggle", VenueAlias.alias_name == squiggle_alias
                )
            ).scalar_one_or_none()
            canonical_alias = session.execute(
                sa.select(VenueAlias).where(
                    VenueAlias.source == "afltables", VenueAlias.alias_name == afltables_slug
                )
            ).scalar_one_or_none()
            if dup_alias is None or canonical_alias is None:
                continue
            if dup_alias.venue_id == canonical_alias.venue_id:
                continue

            duplicate = session.get(Venue, dup_alias.venue_id)
            canonical = session.get(Venue, canonical_alias.venue_id)
            _merge_venue(session, duplicate, canonical)
            merged += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return merged
