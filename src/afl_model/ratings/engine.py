from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Dict, Optional

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Match, ModelVersion, Team, TeamRatingHistory, Venue
from afl_model.ratings.attack_defence import update_attack_defence
from afl_model.ratings.config import RatingsConfig, load_ratings_config
from afl_model.ratings.elo import apply_season_regression, expected_home_win_probability, update_ratings
from afl_model.ratings.form import update_form
from afl_model.ratings.geo_reference import travel_distance_km

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


def _rest_days(last_match_date: Optional[date], this_match_date: date, baseline_days: int) -> Optional[float]:
    if last_match_date is None:
        return None
    return float((this_match_date - last_match_date).days - baseline_days)


def run_ratings_engine(version_name: Optional[str] = None, notes: Optional[str] = None) -> RatingsRunSummary:
    """Walk every completed match in strict chronological order, maintaining
    Elo/attack/defence/form state per team, and record a pre-match snapshot
    of that state for every team in every match (TeamRatingHistory).

    "Pre-match" is the operative word: the row written for a match reflects
    only what was known *before* that match was played — this is what makes
    the history usable for honest, no-lookahead backtesting later (Stage 7).
    Ratings are then updated using the match's actual result before moving
    to the next match.

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

        team_states: Dict[int, TeamState] = {}
        league_avg_score = config.attack_defence.starting_league_avg_score
        history_rows = []

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

            rest_home = _rest_days(home_state.last_match_date, match.match_date, config.rest_baseline_days)
            rest_away = _rest_days(away_state.last_match_date, match.match_date, config.rest_baseline_days)

            travel_home = travel_away = None
            if config.travel_enabled and venue is not None:
                travel_home = travel_distance_km(
                    home_team.home_latitude, home_team.home_longitude, venue.latitude, venue.longitude
                )
                travel_away = travel_distance_km(
                    away_team.home_latitude, away_team.home_longitude, venue.latitude, venue.longitude
                )

            # Snapshot BEFORE this match's result is applied — see docstring.
            history_rows.append(TeamRatingHistory(
                match_id=match.id, team_id=match.home_team_id, model_version_id=model_version_id,
                elo_rating=home_state.elo, attack_rating=home_state.attack,
                defence_rating=home_state.defence, form_rating=home_state.form,
                rest_adjustment=rest_home, travel_adjustment=travel_home, injury_adjustment=None,
            ))
            history_rows.append(TeamRatingHistory(
                match_id=match.id, team_id=match.away_team_id, model_version_id=model_version_id,
                elo_rating=away_state.elo, attack_rating=away_state.attack,
                defence_rating=away_state.defence, form_rating=away_state.form,
                rest_adjustment=rest_away, travel_adjustment=travel_away, injury_adjustment=None,
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

        session.add_all(history_rows)
        session.commit()

        summary = RatingsRunSummary(
            version_name=version_name, matches_processed=len(matches),
            teams_seen=len(team_states), final_league_avg_score=league_avg_score,
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
