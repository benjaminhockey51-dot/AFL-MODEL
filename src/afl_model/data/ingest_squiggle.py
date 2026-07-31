from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from afl_model.data.match_reconciliation import MatchUpsertOutcome, NaturalKey, upsert_match
from afl_model.data.sources.squiggle import SquiggleClient
from afl_model.data.team_venue_resolution import resolve_team, resolve_venue
from afl_model.db.connection import get_session
from afl_model.db.models import Season

logger = logging.getLogger(__name__)

SOURCE = "squiggle"


@dataclass
class IngestSummary:
    year: int
    games_seen: int = 0
    matches_created: int = 0
    matches_linked_existing: int = 0
    matches_resynced: int = 0
    venues_auto_created: int = 0


def ingest_season(
    year: int, round_number: Optional[int] = None, client: Optional[SquiggleClient] = None
) -> IngestSummary:
    """Fetch a season's fixtures/results from Squiggle and upsert them.

    Safe to re-run at any time (e.g. weekly, to pick up newly completed
    rounds) — matches are reconciled via afl_model.data.match_reconciliation,
    so a match already created by another source (e.g. AFL Tables) is
    enriched in place rather than duplicated. `client` is injectable for
    testing.
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
            home_team = resolve_team(session, SOURCE, game["hteam"])
            away_team = resolve_team(session, SOURCE, game["ateam"])
            venue, venue_created = resolve_venue(session, SOURCE, game.get("venue") or "")
            if venue_created:
                summary.venues_auto_created += 1

            match_datetime = datetime.strptime(game["date"], "%Y-%m-%d %H:%M:%S")
            is_complete = game.get("complete") == 100

            natural_key = NaturalKey(
                season_year=game["year"],
                match_date=match_datetime.date(),
                home_team_id=home_team.id,
                away_team_id=away_team.id,
            )
            fields = dict(
                round_number=game["round"],
                round_name=game["roundname"],
                is_final=bool(game.get("is_final")),
                venue_id=venue.id if venue else None,
                match_datetime=match_datetime,
                home_goals=game["hgoals"] if is_complete else None,
                home_behinds=game["hbehinds"] if is_complete else None,
                home_points=game["hscore"] if is_complete else None,
                away_goals=game["agoals"] if is_complete else None,
                away_behinds=game["abehinds"] if is_complete else None,
                away_points=game["ascore"] if is_complete else None,
            )

            _, outcome = upsert_match(session, SOURCE, str(game["id"]), natural_key, fields)
            if outcome is MatchUpsertOutcome.CREATED:
                summary.matches_created += 1
            elif outcome is MatchUpsertOutcome.LINKED_EXISTING:
                summary.matches_linked_existing += 1
            else:
                summary.matches_resynced += 1

            # Committed per-game (see ingest_afltables for the same
            # pattern) — cheap here since fixtures arrive in one API
            # response, but keeps both ingestion pipelines consistent and
            # means a DB-side interruption doesn't discard a whole season.
            session.commit()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Ingest complete for %d: %d created, %d linked to existing matches, "
        "%d resynced, %d venues auto-created",
        year, summary.matches_created, summary.matches_linked_existing,
        summary.matches_resynced, summary.venues_auto_created,
    )
    return summary
