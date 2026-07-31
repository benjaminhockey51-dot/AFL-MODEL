from __future__ import annotations

import logging
from typing import Optional, Tuple

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.db.models import Team, TeamAlias, Venue, VenueAlias

logger = logging.getLogger(__name__)


def resolve_team(session: Session, source: str, source_team_name: str) -> Team:
    """Resolve a source-specific team name to the canonical Team.

    Deliberately fails loudly rather than guessing: an unresolved team name
    means the alias seed is incomplete (or the source renamed a team), and
    either case needs a human to add the mapping — never a silent fuzzy
    match that could misattribute a match to the wrong club.
    """
    alias = session.execute(
        sa.select(TeamAlias).where(TeamAlias.source == source, TeamAlias.alias_name == source_team_name)
    ).scalar_one_or_none()
    if alias is None:
        raise ValueError(
            f"No {source} team alias for '{source_team_name}'. "
            f"Add it to the seed data in afl_model.db.seed and re-run seeding."
        )
    return alias.team


def resolve_venue(
    session: Session, source: str, source_venue_name: str
) -> Tuple[Optional[Venue], bool]:
    """Resolve a source-specific venue name to a canonical Venue, creating
    one automatically on first sight from this source. Returns (venue, was_auto_created).

    Callers that know the true canonical identity for a given source string
    (e.g. afl_model.data.ingest_afltables reconciling Squiggle's inconsistent
    sponsor-name venues against AFL Tables' stable slugs) should resolve
    that mapping *before* calling this, and pass the canonical alias
    directly — this function only handles "have we seen this exact string
    from this exact source before."
    """
    if not source_venue_name:
        return None, False

    alias = session.execute(
        sa.select(VenueAlias).where(
            VenueAlias.source == source, VenueAlias.alias_name == source_venue_name
        )
    ).scalar_one_or_none()
    if alias is not None:
        return alias.venue, False

    existing_venue = session.execute(
        sa.select(Venue).where(Venue.name == source_venue_name)
    ).scalar_one_or_none()
    venue = existing_venue or Venue(name=source_venue_name)
    if existing_venue is None:
        session.add(venue)
        session.flush()  # assign venue.id before the alias FK needs it
    session.add(VenueAlias(venue_id=venue.id, source=source, alias_name=source_venue_name))
    logger.warning(
        "Auto-created venue '%s' from %s data — verify it isn't a renamed "
        "duplicate of an existing venue.",
        source_venue_name, source,
    )
    return venue, True


def link_venue_alias(session: Session, venue: Venue, source: str, alias_name: str) -> None:
    """Attach an additional (source, alias_name) pointing at an *existing* venue.

    Used for verified reconciliation — e.g. linking Squiggle's "Marvel
    Stadium" to the same canonical Venue as AFL Tables' "docklands" slug —
    where the identity was confirmed by cross-referencing real match
    records, not guessed.
    """
    existing = session.execute(
        sa.select(VenueAlias).where(VenueAlias.source == source, VenueAlias.alias_name == alias_name)
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(VenueAlias(venue_id=venue.id, source=source, alias_name=alias_name))
