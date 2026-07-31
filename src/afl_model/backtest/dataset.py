from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.db.models import Match, ModelVersion, Team, TeamRatingHistory, Venue
from afl_model.models.prediction_math import TeamPredictionInputs


@dataclass(frozen=True)
class BacktestMatch:
    match_id: int
    season_year: int
    venue_id: Optional[int]
    venue_name: Optional[str]
    home_team_id: int
    away_team_id: int
    home_team_name: str
    away_team_name: str
    home: TeamPredictionInputs
    away: TeamPredictionInputs
    home_points: int
    away_points: int
    league_avg_score: float


def load_home_ground_advantage(model_version: ModelVersion) -> float:
    """Read home_ground_advantage from the run's own recorded config, not
    the current config.yaml — a model version must be evaluated with the
    settings that actually produced it, even if config.yaml has since
    changed (exactly what config_snapshot exists for).
    """
    config = json.loads(model_version.config_snapshot)
    return config["elo"]["home_ground_advantage"]


def load_backtest_dataset(session: Session, model_version_id: int) -> List[BacktestMatch]:
    """Reconstructs, for every completed match a ratings run processed,
    exactly the pre-match state that would have been used to predict it —
    sourced entirely from TeamRatingHistory's stored snapshots, never from
    CurrentTeamRating (which reflects hindsight). This *is* the no-lookahead
    property: nothing here was computed with knowledge of anything that
    happened on or after the match being evaluated.
    """
    teams_by_id = {t.id: t for t in session.execute(sa.select(Team)).scalars().all()}
    venues_by_id = {v.id: v for v in session.execute(sa.select(Venue)).scalars().all()}

    rows = session.execute(
        sa.select(TeamRatingHistory, Match)
        .join(Match, TeamRatingHistory.match_id == Match.id)
        .where(
            TeamRatingHistory.model_version_id == model_version_id,
            Match.home_points.is_not(None), Match.away_points.is_not(None),
        )
        .order_by(Match.match_date, Match.match_datetime, Match.id)
    ).all()

    by_match: Dict[int, Dict[int, TeamRatingHistory]] = {}
    match_by_id: Dict[int, Match] = {}
    for rating_row, match in rows:
        by_match.setdefault(match.id, {})[rating_row.team_id] = rating_row
        match_by_id[match.id] = match

    ordered_match_ids = sorted(
        match_by_id.keys(),
        key=lambda mid: (match_by_id[mid].match_date, match_by_id[mid].match_datetime or match_by_id[mid].match_date),
    )

    games_played: Dict[int, int] = {}
    dataset = []
    for match_id in ordered_match_ids:
        match = match_by_id[match_id]
        pair = by_match[match_id]
        home_row = pair.get(match.home_team_id)
        away_row = pair.get(match.away_team_id)
        if home_row is None or away_row is None:
            continue  # defensive — shouldn't happen if the ratings run completed cleanly

        home_games = games_played.get(match.home_team_id, 0)
        away_games = games_played.get(match.away_team_id, 0)
        venue = venues_by_id.get(match.venue_id) if match.venue_id else None

        dataset.append(BacktestMatch(
            match_id=match.id,
            season_year=match.season_year,
            venue_id=match.venue_id,
            venue_name=venue.name if venue else None,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            home_team_name=teams_by_id[match.home_team_id].name,
            away_team_name=teams_by_id[match.away_team_id].name,
            home=TeamPredictionInputs(
                elo=home_row.elo_rating, attack=home_row.attack_rating, defence=home_row.defence_rating,
                form=home_row.form_rating, rest_days=home_row.rest_adjustment,
                travel_km=home_row.travel_adjustment, games_played=home_games,
            ),
            away=TeamPredictionInputs(
                elo=away_row.elo_rating, attack=away_row.attack_rating, defence=away_row.defence_rating,
                form=away_row.form_rating, rest_days=away_row.rest_adjustment,
                travel_km=away_row.travel_adjustment, games_played=away_games,
            ),
            home_points=match.home_points,
            away_points=match.away_points,
            league_avg_score=home_row.league_avg_score_before,
        ))

        games_played[match.home_team_id] = home_games + 1
        games_played[match.away_team_id] = away_games + 1

    return dataset
