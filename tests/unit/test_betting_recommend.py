from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

import afl_model.betting.recommend as recommend_module
from afl_model.betting.config import BettingConfig
from afl_model.betting.recommend import assess_match_value, assess_round_value
from afl_model.db.models import Match, ModelVersion, Odds, Prediction, Season, Team


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _make_match(session, home, away, match_date, round_number=22) -> Match:
    match = Match(
        created_by_source="squiggle", created_by_source_match_id=f"{home.id}-{away.id}-{match_date}",
        season_year=match_date.year, round_number=round_number, round_name=f"Round {round_number}", is_final=False,
        match_date=match_date, home_team_id=home.id, away_team_id=away.id,
    )
    session.add(match)
    session.flush()
    return match


def _make_prediction(session, match, model_version, home_win_probability, winner_team_id):
    session.add(Prediction(
        match_id=match.id, model_version_id=model_version.id,
        predicted_winner_team_id=winner_team_id, predicted_margin=10.0, predicted_line=10.0,
        predicted_total=170.0, home_win_probability=home_win_probability, confidence=50.0,
    ))


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25))
    version = ModelVersion(name="test-version", config_snapshot="{}")
    session.add(version)
    session.flush()
    session.commit()
    return session, richmond, adelaide, match, version


def test_assess_match_value_none_when_no_prediction(setup):
    session, richmond, adelaide, match, version = setup
    result = assess_match_value(session, match, version.id)
    assert result is None


def test_assess_match_value_none_when_no_odds(setup):
    session, richmond, adelaide, match, version = setup
    _make_prediction(session, match, version, 0.6, richmond.id)
    session.commit()

    result = assess_match_value(session, match, version.id)
    assert result is None


def test_assess_match_value_recommends_when_value_exists(setup, monkeypatch):
    session, richmond, adelaide, match, version = setup
    _make_prediction(session, match, version, 0.6, richmond.id)
    session.add(Odds(
        match_id=match.id, bookmaker="TestBook", snapshot_type="close",
        home_decimal_odds=3.00, away_decimal_odds=1.40, source="fakeodds",
    ))
    session.commit()

    monkeypatch.setattr(
        recommend_module, "load_betting_config",
        lambda: BettingConfig(odds_source=None, min_edge_threshold=0.05),
    )
    result = assess_match_value(session, match, version.id)

    assert result is not None
    assert result.recommendation == "Bet Home"


def test_assess_round_value_reports_no_prediction_honestly(setup):
    session, richmond, adelaide, match, version = setup
    rows = assess_round_value(2026, 22, version_name="test-version")

    assert len(rows) == 1
    assert rows[0].predicted_winner == "No prediction yet"
    assert rows[0].recommendation == "No Bet"


def test_assess_round_value_reports_no_odds_honestly(setup):
    session, richmond, adelaide, match, version = setup
    _make_prediction(session, match, version, 0.6, richmond.id)
    session.commit()

    rows = assess_round_value(2026, 22, version_name="test-version")

    assert len(rows) == 1
    assert rows[0].predicted_winner == "Richmond"
    assert rows[0].recommendation == "No Bet (no odds available)"


def test_assess_round_value_raises_for_no_matches(setup):
    with pytest.raises(ValueError, match="No matches found"):
        assess_round_value(2026, 99, version_name="test-version")
