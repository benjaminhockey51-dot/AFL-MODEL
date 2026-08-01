from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.db.connection import get_session
from afl_model.db.models import (
    CurrentTeamRating,
    Match,
    ModelVersion,
    Prediction,
    Team,
    TeamRatingHistory,
    Venue,
)
from afl_model.models.config import load_prediction_config
from afl_model.models.prediction_math import PredictionConfig, TeamPredictionInputs, compute_prediction
from afl_model.ratings.config import load_ratings_config
from afl_model.ratings.elo import EloConfig
from afl_model.ratings.geo_reference import travel_distance_km
from afl_model.ratings.rest import rest_days_adjustment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoundPredictionRow:
    """Human-readable prediction summary for display — built while the
    session is still open, so callers (e.g. the CLI) don't need one.
    """

    match_id: int
    home_team: str
    away_team: str
    predicted_winner: str
    predicted_margin: float
    predicted_line: float
    predicted_total: float
    home_win_probability: float
    confidence: float


def get_model_version(session: Session, version_name: Optional[str] = None) -> ModelVersion:
    """The named ModelVersion, or the most recently created one if no name
    is given — the latest ratings run is the sensible default rating
    source for a genuinely upcoming match.
    """
    if version_name is not None:
        version = session.execute(
            sa.select(ModelVersion).where(ModelVersion.name == version_name)
        ).scalar_one_or_none()
        if version is None:
            raise ValueError(f"No model version named '{version_name}'.")
        return version

    version = session.execute(
        sa.select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if version is None:
        raise ValueError("No ratings have been computed yet — run `afl-model run-ratings` first.")
    return version


def _team_prediction_inputs(
    session: Session, team_id: int, model_version: ModelVersion,
    match_date, venue: Optional[Venue], elo_config: EloConfig, rest_baseline_days: int, travel_enabled: bool,
) -> TeamPredictionInputs:
    current = session.execute(
        sa.select(CurrentTeamRating).where(
            CurrentTeamRating.team_id == team_id, CurrentTeamRating.model_version_id == model_version.id,
        )
    ).scalar_one_or_none()

    games_played = session.execute(
        sa.select(sa.func.count()).select_from(TeamRatingHistory).where(
            TeamRatingHistory.team_id == team_id, TeamRatingHistory.model_version_id == model_version.id,
        )
    ).scalar_one()

    if current is None:
        # No rating recorded for this team under this model version — either
        # a genuinely new team, or the ratings engine hasn't been re-run
        # since. Starting values are the honest fallback, not a guess, but
        # it means this prediction rests on unraced ratings — confidence
        # (via games_played=0) will reflect that.
        logger.warning(
            "No current rating for team_id=%d under model version '%s' — using starting values.",
            team_id, model_version.name,
        )
        elo, attack, defence, form, last_match_date = elo_config.starting_rating, 0.0, 0.0, 0.0, None
    else:
        elo = current.elo_rating
        attack = current.attack_rating
        defence = current.defence_rating
        form = current.form_rating
        last_match_date = current.last_match_date

    rest_days = rest_days_adjustment(last_match_date, match_date, rest_baseline_days)

    travel_km = None
    if travel_enabled and venue is not None:
        team = session.get(Team, team_id)
        travel_km = travel_distance_km(team.home_latitude, team.home_longitude, venue.latitude, venue.longitude)

    return TeamPredictionInputs(
        elo=elo, attack=attack, defence=defence, form=form,
        rest_days=rest_days, travel_km=travel_km, games_played=games_played,
    )


def predict_match(
    session: Session, match: Match, model_version: ModelVersion, prediction_config: PredictionConfig,
) -> Prediction:
    """Generate (or refresh) a prediction for one match, using the given
    model version's current rating state — never bookmaker odds, which
    this function has no access to at all.
    """
    ratings_config = load_ratings_config()
    venue = session.get(Venue, match.venue_id) if match.venue_id else None

    home_inputs = _team_prediction_inputs(
        session, match.home_team_id, model_version, match.match_date, venue,
        ratings_config.elo, ratings_config.rest_baseline_days, ratings_config.travel_enabled,
    )
    away_inputs = _team_prediction_inputs(
        session, match.away_team_id, model_version, match.match_date, venue,
        ratings_config.elo, ratings_config.rest_baseline_days, ratings_config.travel_enabled,
    )

    league_avg_score = model_version.final_league_avg_score
    if league_avg_score is None:
        league_avg_score = ratings_config.attack_defence.starting_league_avg_score

    output = compute_prediction(
        home_inputs, away_inputs, ratings_config.elo.home_ground_advantage, league_avg_score, prediction_config,
    )
    predicted_winner_team_id = match.home_team_id if output.predicted_winner == "home" else match.away_team_id

    existing = session.execute(
        sa.select(Prediction).where(
            Prediction.match_id == match.id, Prediction.model_version_id == model_version.id,
        )
    ).scalar_one_or_none()

    fields = dict(
        predicted_winner_team_id=predicted_winner_team_id,
        predicted_margin=output.predicted_margin,
        predicted_line=output.predicted_line,
        predicted_total=output.predicted_total,
        home_win_probability=output.home_win_probability,
        confidence=output.confidence,
    )
    if existing is None:
        prediction = Prediction(match_id=match.id, model_version_id=model_version.id, **fields)
        session.add(prediction)
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
        prediction = existing

    return prediction


def predict_round(
    year: int, round_number: int, version_name: Optional[str] = None, skip_played: bool = False,
) -> List[RoundPredictionRow]:
    """Generate (and persist) predictions for every match in a round. Works
    for a round that hasn't been played yet (the normal case — predicting
    an upcoming round) as well as a past round (useful for spot-checking);
    either way it uses the given model version's *current* state, so
    re-predicting a played round reflects hindsight, not what would have
    been known at the time — that distinction matters for Stage 7's
    backtesting, which predicts from TeamRatingHistory's pre-match
    snapshots instead.

    `skip_played`, when True, excludes matches that already have a result
    from prediction generation entirely — for unattended automation, which
    has no one to notice a hindsight-biased "prediction" for a game that's
    already been played (see afl_model.automation.pipeline.run_auto_update).
    Manual/CLI callers leave this False, preserving the spot-check use case
    above.
    """
    session = get_session()
    try:
        model_version = get_model_version(session, version_name)
        prediction_config = load_prediction_config()

        conditions = [Match.season_year == year, Match.round_number == round_number]
        if skip_played:
            conditions.append(Match.home_points.is_(None))

        matches = session.execute(
            sa.select(Match).where(*conditions).order_by(Match.match_date, Match.match_datetime)
        ).scalars().all()
        if not matches:
            raise ValueError(f"No matches found for {year} round {round_number}.")

        rows = []
        for match in matches:
            prediction = predict_match(session, match, model_version, prediction_config)
            home_team = session.get(Team, match.home_team_id)
            away_team = session.get(Team, match.away_team_id)
            winner_name = home_team.name if prediction.predicted_winner_team_id == home_team.id else away_team.name
            rows.append(RoundPredictionRow(
                match_id=match.id, home_team=home_team.name, away_team=away_team.name,
                predicted_winner=winner_name, predicted_margin=prediction.predicted_margin,
                predicted_line=prediction.predicted_line, predicted_total=prediction.predicted_total,
                home_win_probability=prediction.home_win_probability, confidence=prediction.confidence,
            ))

        session.commit()
        return rows
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
