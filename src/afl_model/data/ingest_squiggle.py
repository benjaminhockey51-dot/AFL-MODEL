from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.data.sources.squiggle import SquiggleClient
from afl_model.db.connection import get_session
from afl_model.db.models import Match, Season, Team, TeamAlias, Venue, VenueAlias

logger = logging.getLogger(__name__)

SOURCE = "squiggle"


@dataclass
class IngestSummary:
    year: int
    games_seen: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    venues_auto_created: int = 0


def resolve_team(session: Session, source_team_name: str) -> Team:
    """Resolve a source-specific team name to the canonical Team.

    Deliberately fails loudly rather than guessing: an unresolved team name
    means either the alias seed is incomplete or the source has renamed a
    team, and either case needs a human to add the mapping, not a silent
    fuzzy match that could misattribute a match to the wrong club.
    """
    alias = session.execute(
        sa.select(TeamAlias).where(
            TeamAlias.source == SOURCE, TeamAlias.alias_name == source_team_name
        )
    ).scalar_one_or_none()
    if alias is None:
        raise ValueError(
            f"No Squiggle team alias for '{source_team_name}'. "
            f"Add it to SQUIGGLE_TEAM_ALIASES in afl_model.db.seed and re-run seeding."
        )
    return alias.team


def resolve_venue(session: Session, source_venue_name: str, summary: IngestSummary) -> Optional[Venue]:
    """Resolve a source-specific venue name to a canonical Venue, creating
    one automatically on first sight.

    KNOWN LIMITATION: Squiggle uses inconsistent naming for the same
    physical venue across seasons (e.g. "Perth Stadium" vs "Optus Stadium",
    "Docklands" vs "Marvel Stadium", "Kardinia Park" vs "GMHBA Stadium").
    Rather than guess which strings refer to the same ground, each distinct
    string gets its own Venue row for now. Deliberate reconciliation
    (merging true duplicates, backed by geocoding for travel-distance
    calculations) is planned as part of Stage 3/4 venue work, not guessed
    here.
    """
    if not source_venue_name:
        return None

    alias = session.execute(
        sa.select(VenueAlias).where(
            VenueAlias.source == SOURCE, VenueAlias.alias_name == source_venue_name
        )
    ).scalar_one_or_none()
    if alias is not None:
        return alias.venue

    existing_venue = session.execute(
        sa.select(Venue).where(Venue.name == source_venue_name)
    ).scalar_one_or_none()
    venue = existing_venue or Venue(name=source_venue_name)
    if existing_venue is None:
        session.add(venue)
        session.flush()  # assign venue.id before the alias FK needs it
    session.add(VenueAlias(venue_id=venue.id, source=SOURCE, alias_name=source_venue_name))
    summary.venues_auto_created += 1
    logger.warning(
        "Auto-created venue '%s' from Squiggle data — verify it isn't a "
        "renamed duplicate of an existing venue.",
        source_venue_name,
    )
    return venue


def _upsert_match(session: Session, game: Dict[str, Any], summary: IngestSummary) -> None:
    existing = session.execute(
        sa.select(Match).where(Match.source == SOURCE, Match.source_match_id == str(game["id"]))
    ).scalar_one_or_none()

    home_team = resolve_team(session, game["hteam"])
    away_team = resolve_team(session, game["ateam"])
    venue = resolve_venue(session, game.get("venue") or "", summary)

    match_datetime = datetime.strptime(game["date"], "%Y-%m-%d %H:%M:%S")
    is_complete = game.get("complete") == 100

    fields = dict(
        season_year=game["year"],
        round_number=game["round"],
        round_name=game["roundname"],
        is_final=bool(game.get("is_final")),
        venue_id=venue.id if venue else None,
        match_date=match_datetime.date(),
        match_datetime=match_datetime,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        home_goals=game["hgoals"] if is_complete else None,
        home_behinds=game["hbehinds"] if is_complete else None,
        home_points=game["hscore"] if is_complete else None,
        away_goals=game["agoals"] if is_complete else None,
        away_behinds=game["abehinds"] if is_complete else None,
        away_points=game["ascore"] if is_complete else None,
    )

    if existing is None:
        session.add(Match(source=SOURCE, source_match_id=str(game["id"]), **fields))
        summary.matches_created += 1
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
        summary.matches_updated += 1


def ingest_season(
    year: int, round_number: Optional[int] = None, client: Optional[SquiggleClient] = None
) -> IngestSummary:
    """Fetch a season's fixtures/results from Squiggle and upsert them.

    Safe to re-run at any time (e.g. weekly, to pick up newly completed
    rounds) — matches are matched on (source, source_match_id) and updated
    in place rather than duplicated. `client` is injectable for testing.
    """
    summary = IngestSummary(year=year)
    client = client or SquiggleClient()
    session = get_session()
    try:
        if session.get(Season, year) is None:
            session.add(Season(year=year))
            session.flush()

        games = client.get_games(year=year, round_number=round_number)
        summary.games_seen = len(games)
        logger.info("Fetched %d Squiggle games for %d%s", len(games), year,
                     f" round {round_number}" if round_number else "")

        for game in games:
            _upsert_match(session, game, summary)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Ingest complete for %d: %d created, %d updated, %d venues auto-created",
        year, summary.matches_created, summary.matches_updated, summary.venues_auto_created,
    )
    return summary
