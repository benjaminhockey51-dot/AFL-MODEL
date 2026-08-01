from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import sqlalchemy as sa

from afl_model.betting.recommend import assess_round_value
from afl_model.db.connection import get_session
from afl_model.db.models import CurrentTeamRating, Match, Team, Venue
from afl_model.models.config import load_prediction_config
from afl_model.models.predict import get_model_version, predict_round
from afl_model.ratings.config import load_ratings_config
from afl_model.ratings.geo_reference import travel_distance_km
from afl_model.reporting.explain import ExplanationInputs, explain_prediction

# Display/categorization thresholds — these affect only how results are
# grouped in the report, not any prediction or accuracy calculation, so
# they're plain constants here rather than config.yaml entries.
_AVOID_CONFIDENCE_THRESHOLD = 20.0
_TOP_N = 3


@dataclass(frozen=True)
class MatchReportRow:
    match_id: int
    home_team: str
    away_team: str
    predicted_winner: str
    predicted_margin: float
    predicted_line: float
    predicted_total: float
    home_win_probability: float
    confidence: float
    recommendation: str
    bookmaker: Optional[str]
    edge: Optional[float]
    explanation: str


@dataclass(frozen=True)
class RoundReport:
    year: int
    round_number: int
    matches: List[MatchReportRow]
    highest_confidence: List[MatchReportRow]
    best_value: List[MatchReportRow]
    games_to_avoid: List[MatchReportRow]


def build_round_report(
    year: int, round_number: int, version_name: Optional[str] = None, snapshot_type: str = "close",
) -> RoundReport:
    """The full "Predict Round N" experience: generates (and persists)
    predictions, compares them against whatever odds already exist, and
    builds a plain-English explanation for each match from the actual
    numbers behind it — then groups matches into highest-confidence,
    best-value, and games-to-avoid.
    """
    prediction_rows = predict_round(year, round_number, version_name)
    value_rows = {v.match_id: v for v in assess_round_value(year, round_number, version_name, snapshot_type)}

    ratings_config = load_ratings_config()
    prediction_config = load_prediction_config()

    session = get_session()
    try:
        model_version = get_model_version(session, version_name)
        rows = []

        for pred_row in prediction_rows:
            match = session.get(Match, pred_row.match_id)
            home_team = session.get(Team, match.home_team_id)
            away_team = session.get(Team, match.away_team_id)
            venue = session.get(Venue, match.venue_id) if match.venue_id else None

            home_rating = session.execute(
                sa.select(CurrentTeamRating).where(
                    CurrentTeamRating.team_id == home_team.id, CurrentTeamRating.model_version_id == model_version.id,
                )
            ).scalar_one_or_none()
            away_rating = session.execute(
                sa.select(CurrentTeamRating).where(
                    CurrentTeamRating.team_id == away_team.id, CurrentTeamRating.model_version_id == model_version.id,
                )
            ).scalar_one_or_none()

            home_travel = away_travel = None
            if venue is not None:
                if home_rating is not None:
                    home_travel = travel_distance_km(
                        home_team.home_latitude, home_team.home_longitude, venue.latitude, venue.longitude,
                    )
                if away_rating is not None:
                    away_travel = travel_distance_km(
                        away_team.home_latitude, away_team.home_longitude, venue.latitude, venue.longitude,
                    )

            explanation = explain_prediction(ExplanationInputs(
                home_team=home_team.name, away_team=away_team.name,
                predicted_winner="home" if pred_row.predicted_winner == home_team.name else "away",
                home_win_probability=pred_row.home_win_probability,
                predicted_margin=pred_row.predicted_margin,
                home_elo=home_rating.elo_rating if home_rating else ratings_config.elo.starting_rating,
                away_elo=away_rating.elo_rating if away_rating else ratings_config.elo.starting_rating,
                home_attack=home_rating.attack_rating if home_rating else 0.0,
                away_attack=away_rating.attack_rating if away_rating else 0.0,
                home_defence=home_rating.defence_rating if home_rating else 0.0,
                away_defence=away_rating.defence_rating if away_rating else 0.0,
                home_travel_km=home_travel, away_travel_km=away_travel,
                travel_elo_scale_per_100km=prediction_config.travel_elo_scale_per_100km,
                confidence=pred_row.confidence,
            ))

            value = value_rows.get(pred_row.match_id)
            recommendation = value.recommendation if value else "No Bet"
            edge = None
            if value is not None and recommendation == "Bet Home":
                edge = value.home_edge
            elif value is not None and recommendation == "Bet Away":
                edge = value.away_edge

            rows.append(MatchReportRow(
                match_id=pred_row.match_id, home_team=pred_row.home_team, away_team=pred_row.away_team,
                predicted_winner=pred_row.predicted_winner, predicted_margin=pred_row.predicted_margin,
                predicted_line=pred_row.predicted_line, predicted_total=pred_row.predicted_total,
                home_win_probability=pred_row.home_win_probability, confidence=pred_row.confidence,
                recommendation=recommendation, bookmaker=value.bookmaker if value else None,
                edge=edge, explanation=explanation,
            ))
    finally:
        session.close()

    highest_confidence = sorted(rows, key=lambda r: -r.confidence)[:_TOP_N]
    best_value = sorted(
        [r for r in rows if r.recommendation in ("Bet Home", "Bet Away")], key=lambda r: -(r.edge or 0.0),
    )[:_TOP_N]
    games_to_avoid = [r for r in rows if r.confidence < _AVOID_CONFIDENCE_THRESHOLD]

    return RoundReport(
        year=year, round_number=round_number, matches=rows,
        highest_confidence=highest_confidence, best_value=best_value, games_to_avoid=games_to_avoid,
    )
