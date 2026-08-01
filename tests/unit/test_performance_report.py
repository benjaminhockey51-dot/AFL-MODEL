from __future__ import annotations

from datetime import date

import pytest

from afl_model.db.models import Match, ModelVersion, Prediction, PredictionResult, Season, Team
from afl_model.reporting.performance_report import build_performance_report


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _make_reconciled_prediction(
    session, version, home, away, season_year, match_date,
    winner_correct, margin_error, total_error, closing_line_diff=None,
):
    match = Match(
        created_by_source="test", created_by_source_match_id=f"{home.id}-{away.id}-{match_date}",
        season_year=season_year, round_number=1, round_name="Round 1", is_final=False,
        match_date=match_date, home_team_id=home.id, away_team_id=away.id, home_points=100, away_points=80,
    )
    session.add(match)
    session.flush()
    prediction = Prediction(
        match_id=match.id, model_version_id=version.id, predicted_winner_team_id=home.id,
        predicted_margin=20.0, predicted_line=20.0, predicted_total=180.0,
        home_win_probability=0.6, confidence=50.0,
    )
    session.add(prediction)
    session.flush()
    session.add(PredictionResult(
        prediction_id=prediction.id, winner_correct=winner_correct,
        margin_error=margin_error, total_error=total_error, closing_line_diff=closing_line_diff,
    ))


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2025))
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    version = ModelVersion(name="test-version", config_snapshot="{}")
    session.add(version)
    session.flush()
    return session, richmond, adelaide, version


def test_build_performance_report_computes_overall_accuracy(setup):
    session, richmond, adelaide, version = setup
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 1), True, 5.0, 3.0)
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 8), False, -10.0, 8.0)
    session.commit()

    report = build_performance_report()

    assert report.overall_n == 2
    assert report.overall_n_decisive == 2
    assert report.overall_win_accuracy == pytest.approx(0.5)
    assert report.overall_margin_mae == pytest.approx((5.0 + 10.0) / 2)
    assert report.overall_total_mae == pytest.approx((3.0 + 8.0) / 2)


def test_build_performance_report_excludes_draws_from_accuracy(setup):
    session, richmond, adelaide, version = setup
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 1), None, 5.0, 3.0)  # draw
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 8), True, 2.0, 1.0)
    session.commit()

    report = build_performance_report()

    assert report.overall_n == 2
    assert report.overall_n_decisive == 1
    assert report.overall_win_accuracy == pytest.approx(1.0)


def test_build_performance_report_by_season_breakdown(setup):
    session, richmond, adelaide, version = setup
    _make_reconciled_prediction(session, version, richmond, adelaide, 2025, date(2025, 3, 1), True, 5.0, 3.0)
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 8), False, 5.0, 3.0)
    session.commit()

    report = build_performance_report()

    seasons = {s.season_year: s for s in report.by_season}
    assert seasons[2025].win_accuracy == pytest.approx(1.0)
    assert seasons[2026].win_accuracy == pytest.approx(0.0)


def test_build_performance_report_mean_closing_line_diff(setup):
    session, richmond, adelaide, version = setup
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 1), True, 5.0, 3.0, closing_line_diff=2.0)
    _make_reconciled_prediction(session, version, richmond, adelaide, 2026, date(2026, 3, 8), True, 5.0, 3.0, closing_line_diff=None)
    session.commit()

    report = build_performance_report()

    assert report.n_with_closing_odds == 1
    assert report.mean_closing_line_diff == pytest.approx(2.0)


def test_build_performance_report_recent_n_limits_window(setup):
    session, richmond, adelaide, version = setup
    for i in range(5):
        _make_reconciled_prediction(
            session, version, richmond, adelaide, 2026, date(2026, 3, 1 + i * 7),
            winner_correct=(i >= 3), margin_error=1.0, total_error=1.0,
        )
    session.commit()

    report = build_performance_report(recent_n=2)

    assert report.recent_n == 2
    # Most recent 2 (by match_date desc) both had winner_correct=True (i=3,4)
    assert report.recent_win_accuracy == pytest.approx(1.0)


def test_build_performance_report_empty_database(db_session):
    report = build_performance_report()
    assert report.overall_n == 0
    assert report.overall_win_accuracy is None
    assert report.by_season == []
