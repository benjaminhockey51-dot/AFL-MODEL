from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Dict, List, Optional

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import CurrentTeamRating, Match, ModelVersion, Team, TeamRatingHistory, Venue
from afl_model.models.prediction_math import TeamPredictionInputs
from afl_model.ratings.attack_defence import update_attack_defence
from afl_model.ratings.config import RatingsConfig, load_ratings_config
from afl_model.ratings.elo import apply_season_regression, expected_home_win_probability, update_ratings
from afl_model.ratings.form import update_form
from afl_model.ratings.geo_reference import travel_distance_km
from afl_model.ratings.rest import rest_days_adjustment

logger = logging.getLogger(__name__)


@dataclass
class TeamState:
    elo: float
    attack: float
    defence: float
    form: float
    last_match_date: Optional[date]
    last_season: Optional[int]

    @staticmethod
    def initial(config: RatingsConfig) -> "TeamState":
        return TeamState(
            elo=config.elo.starting_rating, attack=0.0, defence=0.0, form=0.0,
            last_match_date=None, last_season=None,
        )


@dataclass(frozen=True)
class MatchWalkForwardSnapshot:
    match: Match
    home: TeamPredictionInputs
    away: TeamPredictionInputs
    league_avg_score_before: float


@dataclass(frozen=True)
class WalkForwardResult:
    snapshots: List[MatchWalkForwardSnapshot]
    final_team_states: Dict[int, TeamState]
    final_league_avg_score: float


@dataclass
class RatingsRunSummary:
    version_name: str
    matches_processed: int = 0
    teams_seen: int = 0
    final_league_avg_score: float = 0.0


def _apply_season_regression_if_needed(state: TeamState, season_year: int, config: RatingsConfig) -> TeamState:
    if state.last_season is None or state.last_season == season_year:
        return state
    # Same regression_factor drives Elo and attack/defence — both represent
    # "team strength," just on different scales (1500-centred vs 0-centred),
    # so the same "how much carries over between seasons" belief applies to
    # both. Form is reset outright rather than regressed: a hot/cold streak
    # from before a full off-season isn't meaningful signal for round 1.
    return replace(
        state,
        elo=apply_season_regression(state.elo, config.elo),
        attack=config.elo.season_regression_factor * state.attack,
        defence=config.elo.season_regression_factor * state.defence,
        form=0.0,
    )


def compute_walk_forward(
    matches: List[Match], teams_by_id: Dict[int, Team], venues_by_id: Dict[int, Venue], config: RatingsConfig,
) -> WalkForwardResult:
    """The core walk-forward computation, entirely in memory — no database
    reads or writes. `matches` must already be sorted chronologically.

    This is deliberately separated from run_ratings_engine (which persists
    the result) so Stage 7's hyperparameter search can evaluate hundreds or
    thousands of candidate configs rapidly without writing a fresh
    ModelVersion + thousands of TeamRatingHistory rows to the database for
    every candidate — that would make systematic tuning impractically slow
    and bloat the database with runs nobody will ever look at again.
    """
    team_states: Dict[int, TeamState] = {}
    games_played: Dict[int, int] = {}
    league_avg_score = config.attack_defence.starting_league_avg_score
    snapshots = []

    for match in matches:
        home_state = _apply_season_regression_if_needed(
            team_states.setdefault(match.home_team_id, TeamState.initial(config)),
            match.season_year, config,
        )
        away_state = _apply_season_regression_if_needed(
            team_states.setdefault(match.away_team_id, TeamState.initial(config)),
            match.season_year, config,
        )

        home_team = teams_by_id[match.home_team_id]
        away_team = teams_by_id[match.away_team_id]
        venue = venues_by_id.get(match.venue_id) if match.venue_id else None

        rest_home = rest_days_adjustment(home_state.last_match_date, match.match_date, config.rest_baseline_days)
        rest_away = rest_days_adjustment(away_state.last_match_date, match.match_date, config.rest_baseline_days)

        travel_home = travel_away = None
        if config.travel_enabled and venue is not None:
            travel_home = travel_distance_km(
                home_team.home_latitude, home_team.home_longitude, venue.latitude, venue.longitude
            )
            travel_away = travel_distance_km(
                away_team.home_latitude, away_team.home_longitude, venue.latitude, venue.longitude
            )

        home_games = games_played.get(match.home_team_id, 0)
        away_games = games_played.get(match.away_team_id, 0)

        # Snapshot BEFORE this match's result is applied — the pre-match
        # state a predictor would actually have had access to.
        snapshots.append(MatchWalkForwardSnapshot(
            match=match,
            home=TeamPredictionInputs(
                elo=home_state.elo, attack=home_state.attack, defence=home_state.defence, form=home_state.form,
                rest_days=rest_home, travel_km=travel_home, games_played=home_games,
            ),
            away=TeamPredictionInputs(
                elo=away_state.elo, attack=away_state.attack, defence=away_state.defence, form=away_state.form,
                rest_days=rest_away, travel_km=travel_away, games_played=away_games,
            ),
            league_avg_score_before=league_avg_score,
        ))

        expected_home = expected_home_win_probability(
            home_state.elo, away_state.elo, config.elo.home_ground_advantage
        )
        new_home_elo, new_away_elo = update_ratings(
            home_state.elo, away_state.elo, match.home_points, match.away_points, config.elo
        )

        ad_update = update_attack_defence(
            home_state.attack, home_state.defence, away_state.attack, away_state.defence,
            league_avg_score, match.home_points, match.away_points, config.attack_defence,
        )
        league_avg_score = ad_update.league_avg_score

        margin = match.home_points - match.away_points
        actual_home = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
        new_home_form = update_form(home_state.form, actual_home, expected_home, config.form_ewma_alpha)
        new_away_form = update_form(away_state.form, 1.0 - actual_home, 1.0 - expected_home, config.form_ewma_alpha)

        team_states[match.home_team_id] = TeamState(
            elo=new_home_elo, attack=ad_update.home_attack, defence=ad_update.home_defence,
            form=new_home_form, last_match_date=match.match_date, last_season=match.season_year,
        )
        team_states[match.away_team_id] = TeamState(
            elo=new_away_elo, attack=ad_update.away_attack, defence=ad_update.away_defence,
            form=new_away_form, last_match_date=match.match_date, last_season=match.season_year,
        )

        games_played[match.home_team_id] = home_games + 1
        games_played[match.away_team_id] = away_games + 1

    return WalkForwardResult(
        snapshots=snapshots, final_team_states=team_states, final_league_avg_score=league_avg_score,
    )


def run_ratings_engine(version_name: Optional[str] = None, notes: Optional[str] = None) -> RatingsRunSummary:
    """Loads every completed match, runs compute_walk_forward, and persists
    the result as a new ModelVersion — a pre-match TeamRatingHistory
    snapshot per team per match (never overwritten, what makes honest
    no-lookahead backtesting possible later), plus each team's final
    (post-history) state in CurrentTeamRating, for predicting whatever
    hasn't been played yet.

    Each call creates a brand new ModelVersion and a fresh, independent set
    of history rows — re-running (e.g. after tuning config.yaml) never
    mutates or deletes a prior run, so different rating configurations can
    be compared against each other honestly.
    """
    config = load_ratings_config()
    session = get_session()
    try:
        if version_name is None:
            version_name = f"ratings-{datetime.utcnow():%Y%m%d-%H%M%S}"
        model_version = ModelVersion(name=version_name, config_snapshot=config.to_json(), notes=notes)
        session.add(model_version)
        session.flush()
        model_version_id = model_version.id

        teams_by_id = {t.id: t for t in session.execute(sa.select(Team)).scalars().all()}
        venues_by_id = {v.id: v for v in session.execute(sa.select(Venue)).scalars().all()}
        matches = session.execute(
            sa.select(Match)
            .where(Match.home_points.is_not(None), Match.away_points.is_not(None))
            .order_by(Match.match_date, Match.match_datetime)
        ).scalars().all()

        result = compute_walk_forward(matches, teams_by_id, venues_by_id, config)

        history_rows = []
        for snap in result.snapshots:
            history_rows.append(TeamRatingHistory(
                match_id=snap.match.id, team_id=snap.match.home_team_id, model_version_id=model_version_id,
                elo_rating=snap.home.elo, attack_rating=snap.home.attack,
                defence_rating=snap.home.defence, form_rating=snap.home.form,
                rest_adjustment=snap.home.rest_days, travel_adjustment=snap.home.travel_km, injury_adjustment=None,
                league_avg_score_before=snap.league_avg_score_before,
            ))
            history_rows.append(TeamRatingHistory(
                match_id=snap.match.id, team_id=snap.match.away_team_id, model_version_id=model_version_id,
                elo_rating=snap.away.elo, attack_rating=snap.away.attack,
                defence_rating=snap.away.defence, form_rating=snap.away.form,
                rest_adjustment=snap.away.rest_days, travel_adjustment=snap.away.travel_km, injury_adjustment=None,
                league_avg_score_before=snap.league_avg_score_before,
            ))

        session.add_all(history_rows)
        session.add_all(
            CurrentTeamRating(
                team_id=team_id, model_version_id=model_version_id,
                elo_rating=state.elo, attack_rating=state.attack,
                defence_rating=state.defence, form_rating=state.form,
                last_match_date=state.last_match_date, last_season=state.last_season,
            )
            for team_id, state in result.final_team_states.items()
        )
        model_version.final_league_avg_score = result.final_league_avg_score
        session.commit()

        summary = RatingsRunSummary(
            version_name=version_name, matches_processed=len(result.snapshots),
            teams_seen=len(result.final_team_states), final_league_avg_score=result.final_league_avg_score,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Ratings engine run '%s' complete: %d matches, %d teams, final league avg score %.1f",
        summary.version_name, summary.matches_processed, summary.teams_seen, summary.final_league_avg_score,
    )
    return summary
