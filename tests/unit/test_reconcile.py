from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from afl_model.db.models import Match, ModelVersion, Odds, Prediction, PredictionResult, Season, Team
from afl_model.reporting.reconcile import reconcile_predictions


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _make_match(session, home, away, match_date, home_pts=None, away_pts=None) -> Match:
    match = Match(
        created_by_source="test", created_by_source_match_id=f"{home.id}-{away.id}-{match_date}",
        season_year=match_date.year, round_number=1, round_name="Round 1", is_final=False,
        match_date=match_date, home_team_id=home.id, away_team_id=away.id,
        home_points=home_pts, away_points=away_pts,
    )
    session.add(match)
    session.flush()
    return match


def _make_prediction(session, match, version, predicted_margin, predicted_line, predicted_total, winner_team_id):
    prediction = Prediction(
        match_id=match.id, model_version_id=version.id, predicted_winner_team_id=winner_team_id,
        predicted_margin=predicted_margin, predicted_line=predicted_line, predicted_total=predicted_total,
        home_win_probability=0.6, confidence=50.0,
    )
    session.add(prediction)
    session.flush()
    return prediction


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    version = ModelVersion(name="test-version", config_snapshot="{}")
    session.add(version)
    session.flush()
    session.commit()
    return session, richmond, adelaide, version


def test_reconcile_computes_correct_winner_and_errors(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=100, away_pts=80)
    prediction = _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    summary = reconcile_predictions()

    assert summary.predictions_checked == 1
    assert summary.created == 1

    result = session.execute(sa.select(PredictionResult).where(PredictionResult.prediction_id == prediction.id)).scalar_one()
    assert result.winner_correct is True
    assert result.margin_error == pytest.approx(15.0 - 20.0)  # predicted 15, actual 20
    assert result.total_error == pytest.approx(175.0 - 180.0)  # predicted 175, actual 180


def test_reconcile_marks_incorrect_winner(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=60, away_pts=100)
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    reconcile_predictions()

    result = session.execute(sa.select(PredictionResult)).scalar_one()
    assert result.winner_correct is False


def test_reconcile_treats_draw_as_neither_correct_nor_incorrect(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=90, away_pts=90)
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    reconcile_predictions()

    result = session.execute(sa.select(PredictionResult)).scalar_one()
    assert result.winner_correct is None


def test_reconcile_skips_unplayed_matches(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25))  # no scores yet
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    summary = reconcile_predictions()

    assert summary.predictions_checked == 0
    total = session.execute(sa.select(sa.func.count()).select_from(PredictionResult)).scalar_one()
    assert total == 0


def test_reconcile_computes_closing_line_diff_when_odds_exist(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=100, away_pts=80)
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.add(Odds(
        match_id=match.id, bookmaker="TestBook", snapshot_type="close",
        home_decimal_odds=1.8, away_decimal_odds=2.0, home_line=12.5, source="fakeodds",
    ))
    session.commit()

    reconcile_predictions()

    result = session.execute(sa.select(PredictionResult)).scalar_one()
    assert result.closing_line_diff == pytest.approx(15.0 - 12.5)


def test_reconcile_leaves_closing_line_diff_null_without_odds(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=100, away_pts=80)
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    reconcile_predictions()

    result = session.execute(sa.select(PredictionResult)).scalar_one()
    assert result.closing_line_diff is None


def test_reconcile_is_idempotent(setup):
    session, richmond, adelaide, version = setup
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25), home_pts=100, away_pts=80)
    _make_prediction(session, match, version, predicted_margin=15.0, predicted_line=15.0, predicted_total=175.0, winner_team_id=richmond.id)
    session.commit()

    first = reconcile_predictions()
    second = reconcile_predictions()

    assert first.created == 1
    assert second.created == 0
    assert second.updated == 1
    total = session.execute(sa.select(sa.func.count()).select_from(PredictionResult)).scalar_one()
    assert total == 1
