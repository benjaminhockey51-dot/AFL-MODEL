from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from afl_model.betting.config import load_betting_config
from afl_model.betting.value import OddsQuote, ValueAssessment, best_value_across_quotes
from afl_model.db.connection import get_session
from afl_model.db.models import Match, Odds, Prediction, Team
from afl_model.models.predict import get_model_version


@dataclass(frozen=True)
class RoundValueRow:
    match_id: int
    home_team: str
    away_team: str
    predicted_winner: str
    home_win_probability: Optional[float]
    recommendation: str
    bookmaker: Optional[str]
    home_edge: Optional[float]
    away_edge: Optional[float]
    home_ev: Optional[float]
    away_ev: Optional[float]


def assess_match_value(
    session: Session, match: Match, model_version_id: int, snapshot_type: str = "close",
) -> Optional[ValueAssessment]:
    """Compares an already-generated prediction against already-ingested
    odds for one match. Returns None if either is missing — this never
    generates a prediction or fabricates odds on the fly; both must
    already exist, generated independently of each other (see Prediction's
    docstring on why that separation matters).
    """
    prediction = session.execute(
        sa.select(Prediction).where(
            Prediction.match_id == match.id, Prediction.model_version_id == model_version_id,
        )
    ).scalar_one_or_none()
    if prediction is None:
        return None

    odds_rows = session.execute(
        sa.select(Odds).where(Odds.match_id == match.id, Odds.snapshot_type == snapshot_type)
    ).scalars().all()
    quotes = [
        OddsQuote(bookmaker=o.bookmaker, home_decimal_odds=o.home_decimal_odds, away_decimal_odds=o.away_decimal_odds)
        for o in odds_rows if o.home_decimal_odds is not None and o.away_decimal_odds is not None
    ]
    if not quotes:
        return None

    betting_config = load_betting_config()
    return best_value_across_quotes(prediction.home_win_probability, quotes, betting_config.min_edge_threshold)


def assess_round_value(
    year: int, round_number: int, version_name: Optional[str] = None, snapshot_type: str = "close",
) -> List[RoundValueRow]:
    """Betting-value view of a round, built from whatever predictions and
    odds already exist — never generates either. A match with no
    prediction yet, or no odds yet, is reported honestly rather than
    silently skipped or backfilled with a guess.
    """
    session = get_session()
    try:
        model_version = get_model_version(session, version_name)
        matches = session.execute(
            sa.select(Match).where(Match.season_year == year, Match.round_number == round_number)
            .order_by(Match.match_date, Match.match_datetime)
        ).scalars().all()
        if not matches:
            raise ValueError(f"No matches found for {year} round {round_number}.")

        rows = []
        for match in matches:
            home_team = session.get(Team, match.home_team_id)
            away_team = session.get(Team, match.away_team_id)

            prediction = session.execute(
                sa.select(Prediction).where(
                    Prediction.match_id == match.id, Prediction.model_version_id == model_version.id,
                )
            ).scalar_one_or_none()
            if prediction is None:
                rows.append(RoundValueRow(
                    match_id=match.id, home_team=home_team.name, away_team=away_team.name,
                    predicted_winner="No prediction yet", home_win_probability=None,
                    recommendation="No Bet", bookmaker=None,
                    home_edge=None, away_edge=None, home_ev=None, away_ev=None,
                ))
                continue

            winner_name = home_team.name if prediction.predicted_winner_team_id == home_team.id else away_team.name
            assessment = assess_match_value(session, match, model_version.id, snapshot_type)

            if assessment is None:
                rows.append(RoundValueRow(
                    match_id=match.id, home_team=home_team.name, away_team=away_team.name,
                    predicted_winner=winner_name, home_win_probability=prediction.home_win_probability,
                    recommendation="No Bet (no odds available)", bookmaker=None,
                    home_edge=None, away_edge=None, home_ev=None, away_ev=None,
                ))
                continue

            rows.append(RoundValueRow(
                match_id=match.id, home_team=home_team.name, away_team=away_team.name,
                predicted_winner=winner_name, home_win_probability=prediction.home_win_probability,
                recommendation=assessment.recommendation, bookmaker=assessment.bookmaker,
                home_edge=assessment.home_edge, away_edge=assessment.away_edge,
                home_ev=assessment.home_ev, away_ev=assessment.away_ev,
            ))
        return rows
    finally:
        session.close()
