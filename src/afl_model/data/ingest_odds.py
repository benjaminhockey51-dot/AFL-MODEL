from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import sqlalchemy as sa

from afl_model.betting.odds_client import OddsClient
from afl_model.data.team_venue_resolution import resolve_team
from afl_model.db.connection import get_session
from afl_model.db.models import Match, Odds

logger = logging.getLogger(__name__)


@dataclass
class IngestOddsSummary:
    year: int
    quotes_seen: int = 0
    quotes_matched: int = 0
    quotes_unmatched: int = 0
    odds_created: int = 0
    odds_updated: int = 0


def _find_match(session, home_team_id: int, away_team_id: int, match_date: date) -> Optional[Match]:
    return session.execute(
        sa.select(Match).where(
            Match.home_team_id == home_team_id, Match.away_team_id == away_team_id,
            Match.match_date == match_date,
        )
    ).scalar_one_or_none()


def ingest_odds(
    year: int, client: OddsClient, source_name: str, round_number: Optional[int] = None,
) -> IngestOddsSummary:
    """Fetch odds quotes from `client` and attach them to existing matches.

    Deliberately never creates a Match — odds only enrich matches that a
    fixture source (Squiggle/AFL Tables) already created. A quote for a
    match we don't have on file is logged and skipped, not fabricated.
    `source_name` is the team-alias source key (e.g. "theoddsapi") that
    must already be seeded — team names are resolved through the same
    fail-loud alias mechanism every other source uses.
    """
    summary = IngestOddsSummary(year=year)
    session = get_session()
    try:
        quotes = client.get_odds(year=year, round_number=round_number)
        summary.quotes_seen = len(quotes)

        for quote in quotes:
            home_team = resolve_team(session, source_name, quote.home_team_name)
            away_team = resolve_team(session, source_name, quote.away_team_name)
            match = _find_match(session, home_team.id, away_team.id, quote.commence_time.date())

            if match is None:
                summary.quotes_unmatched += 1
                logger.warning(
                    "No match found for %s v %s on %s (source=%s) — skipped.",
                    quote.home_team_name, quote.away_team_name, quote.commence_time.date(), source_name,
                )
                continue
            summary.quotes_matched += 1

            existing = session.execute(
                sa.select(Odds).where(
                    Odds.match_id == match.id, Odds.bookmaker == quote.bookmaker,
                    Odds.snapshot_type == quote.snapshot_type,
                )
            ).scalar_one_or_none()

            fields = dict(
                home_decimal_odds=quote.home_decimal_odds, away_decimal_odds=quote.away_decimal_odds,
                home_line=quote.home_line, away_line=quote.away_line, total_line=quote.total_line,
                source=source_name,
            )
            if existing is None:
                session.add(Odds(
                    match_id=match.id, bookmaker=quote.bookmaker, snapshot_type=quote.snapshot_type, **fields,
                ))
                summary.odds_created += 1
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
                summary.odds_updated += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Odds ingest complete for %d (%s): %d quotes seen, %d matched, %d unmatched, %d created, %d updated",
        year, source_name, summary.quotes_seen, summary.quotes_matched, summary.quotes_unmatched,
        summary.odds_created, summary.odds_updated,
    )
    return summary
