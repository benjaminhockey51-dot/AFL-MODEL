from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.data.match_reconciliation import MatchUpsertOutcome, NaturalKey, upsert_match
from afl_model.data.sources.afl_tables import AFLTablesClient
from afl_model.data.sources.afl_tables_parser import (
    ScrapedGame,
    ScrapedPlayerStat,
    ScrapedTeamMatchStats,
    parse_match_stats_page,
    parse_season_page,
)
from afl_model.data.team_venue_resolution import resolve_team
from afl_model.db.connection import get_session
from afl_model.db.models import (
    Match,
    Player,
    PlayerMatchStats,
    Season,
    TeamMatchStats,
    Venue,
    VenueAlias,
)

logger = logging.getLogger(__name__)

SOURCE = "afltables"
PLAYER_URL_RE = re.compile(r"players/(.+)\.html")


@dataclass
class IngestSummary:
    year: int
    games_seen: int = 0
    games_incomplete_skipped: int = 0
    matches_created: int = 0
    matches_linked_existing: int = 0
    matches_resynced: int = 0
    venues_auto_created: int = 0
    team_stats_written: int = 0
    player_stats_written: int = 0
    players_created: int = 0


def _resolve_venue(session: Session, game: ScrapedGame, summary: IngestSummary) -> Optional[Venue]:
    """AFL Tables venues are keyed by their stable internal slug (e.g. "mcg"),
    not display text — see afl_model.data.venue_reconciliation for why.
    Falls back to an exact Venue.name match so a venue already created by
    another source (e.g. Squiggle, using the same display string) is reused
    rather than duplicated.
    """
    if not game.venue_slug:
        return None

    alias = session.execute(
        sa.select(VenueAlias).where(
            VenueAlias.source == SOURCE, VenueAlias.alias_name == game.venue_slug
        )
    ).scalar_one_or_none()
    if alias is not None:
        return alias.venue

    existing = session.execute(
        sa.select(Venue).where(Venue.name == game.venue_name)
    ).scalar_one_or_none()
    venue = existing or Venue(name=game.venue_name)
    created = existing is None
    if created:
        session.add(venue)
        session.flush()
        summary.venues_auto_created += 1
        logger.warning(
            "Auto-created venue '%s' (slug=%s) from AFL Tables data — verify it isn't a "
            "renamed duplicate of an existing venue.", game.venue_name, game.venue_slug,
        )
    session.add(VenueAlias(venue_id=venue.id, source=SOURCE, alias_name=game.venue_slug))
    return venue


def _resolve_or_create_player(
    session: Session, player_url: Optional[str], display_name: str, summary: IngestSummary
) -> Player:
    if player_url is None:
        # No profile link — extremely rare (seen for a handful of historical
        # entries); fall back to a synthetic ID from the display name so
        # ingestion doesn't stall, but this is a degraded case worth noting.
        source_player_id = f"unlinked:{display_name}"
        logger.warning("Player '%s' has no AFL Tables profile link; using a synthetic ID.", display_name)
    else:
        match = PLAYER_URL_RE.search(player_url)
        if not match:
            raise ValueError(f"Unrecognized AFL Tables player URL shape: {player_url}")
        source_player_id = match.group(1)

    existing = session.execute(
        sa.select(Player).where(Player.source == SOURCE, Player.source_player_id == source_player_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    if ", " in display_name:
        last_name, first_name = display_name.split(", ", 1)
    else:
        last_name, first_name = display_name, ""

    player = Player(
        first_name=first_name,
        last_name=last_name,
        source=SOURCE,
        source_player_id=source_player_id,
    )
    session.add(player)
    session.flush()
    summary.players_created += 1
    return player


def _ingest_match_stats(
    session: Session, match: Match, match_stats_path: str, year: int,
    client: AFLTablesClient, summary: IngestSummary,
) -> None:
    html = client.get_match_stats_page(match_stats_path, year)
    team_stats_rows, player_stats_rows = parse_match_stats_page(html)

    for row in team_stats_rows:
        _upsert_team_match_stats(session, match, row, summary)
    for row in player_stats_rows:
        _upsert_player_match_stats(session, match, row, summary)


def _upsert_team_match_stats(
    session: Session, match: Match, row: ScrapedTeamMatchStats, summary: IngestSummary
) -> None:
    team = resolve_team(session, SOURCE, row.team_name)
    existing = session.execute(
        sa.select(TeamMatchStats).where(
            TeamMatchStats.match_id == match.id, TeamMatchStats.team_id == team.id
        )
    ).scalar_one_or_none()

    # Only the subset of ScrapedTeamMatchStats fields that TeamMatchStats
    # actually has columns for (it omits jumper-number-only concepts and a
    # few player-level-only stats like brownlow votes).
    fields = {k: v for k, v in row.stats.items() if hasattr(TeamMatchStats, k)}

    if existing is None:
        session.add(TeamMatchStats(match_id=match.id, team_id=team.id, **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
    summary.team_stats_written += 1


def _upsert_player_match_stats(
    session: Session, match: Match, row: ScrapedPlayerStat, summary: IngestSummary
) -> None:
    team = resolve_team(session, SOURCE, row.team_name)
    player = _resolve_or_create_player(session, row.player_url, row.player_name, summary)

    existing = session.execute(
        sa.select(PlayerMatchStats).where(
            PlayerMatchStats.match_id == match.id, PlayerMatchStats.player_id == player.id
        )
    ).scalar_one_or_none()

    fields = dict(row.stats)
    fields["time_on_ground_pct"] = row.time_on_ground_pct

    if existing is None:
        session.add(PlayerMatchStats(match_id=match.id, team_id=team.id, player_id=player.id, **fields))
    else:
        existing.team_id = team.id
        for key, value in fields.items():
            setattr(existing, key, value)
    summary.player_stats_written += 1


def ingest_season(year: int, client: Optional[AFLTablesClient] = None) -> IngestSummary:
    """Fetch a season's results, attendance, and full team/player match
    stats from AFL Tables, reconciling against matches from any other
    source already ingested (see afl_model.data.match_reconciliation).

    Only completed games (with a final score) are processed — AFL Tables'
    value here is historical detail, not live fixtures, which Squiggle
    already covers.
    """
    summary = IngestSummary(year=year)
    client = client or AFLTablesClient()
    session = get_session()
    try:
        if session.get(Season, year) is None:
            session.add(Season(year=year))
            session.flush()

        html = client.get_season_page(year)
        games = parse_season_page(html, year)
        summary.games_seen = len(games)
        logger.info("Parsed %d AFL Tables games for %d", len(games), year)

        for game in games:
            if game.home_points is None or game.away_points is None:
                summary.games_incomplete_skipped += 1
                continue

            home_team = resolve_team(session, SOURCE, game.home_team)
            away_team = resolve_team(session, SOURCE, game.away_team)
            venue = _resolve_venue(session, game, summary)
            match_datetime = (
                datetime.strptime(game.match_date_str, "%a %d-%b-%Y %I:%M %p")
                if game.match_date_str else None
            )
            if match_datetime is None:
                logger.warning(
                    "Skipping %s v %s (round %s, %d): no parseable date.",
                    game.home_team, game.away_team, game.round_name, year,
                )
                continue

            natural_key = NaturalKey(
                season_year=year,
                match_date=match_datetime.date(),
                home_team_id=home_team.id,
                away_team_id=away_team.id,
            )
            fields = dict(
                round_number=game.round_number,
                round_name=game.round_name,
                is_final=game.is_final,
                venue_id=venue.id if venue else None,
                match_datetime=match_datetime,
                home_goals=game.home_goals,
                home_behinds=game.home_behinds,
                home_points=game.home_points,
                away_goals=game.away_goals,
                away_behinds=game.away_behinds,
                away_points=game.away_points,
                attendance=game.attendance,
            )

            # Not match_stats_path — it's absent for a handful of older/edge
            # games, and the natural key is already guaranteed unique.
            source_match_id = f"{year}-{match_datetime.date()}-{home_team.id}-{away_team.id}"
            match, outcome = upsert_match(session, SOURCE, source_match_id, natural_key, fields)
            if outcome is MatchUpsertOutcome.CREATED:
                summary.matches_created += 1
            elif outcome is MatchUpsertOutcome.LINKED_EXISTING:
                summary.matches_linked_existing += 1
            else:
                summary.matches_resynced += 1

            if game.match_stats_path:
                _ingest_match_stats(session, match, game.match_stats_path, year, client, summary)

            # Committed per-game, not once at the end of the season: this
            # is a ~200-request, rate-limited run that takes several
            # minutes, and a single end-of-season transaction would mean
            # any interruption (network blip, process restart) discards
            # all progress. Per-game commits make a resumed run pick up
            # close to where it left off — already-synced games resolve
            # via MatchSourceRef and their AFL Tables page is already
            # cached locally, so re-processing them costs no network time.
            session.commit()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "AFL Tables ingest complete for %d: %d created, %d linked, %d resynced, "
        "%d venues auto-created, %d team-stat rows, %d player-stat rows, %d new players",
        year, summary.matches_created, summary.matches_linked_existing, summary.matches_resynced,
        summary.venues_auto_created, summary.team_stats_written, summary.player_stats_written,
        summary.players_created,
    )
    return summary
