from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from afl_model.db.models import (
    CurrentTeamRating,
    Match,
    ModelVersion,
    Prediction,
    Season,
    Team,
    Venue,
)
from afl_model.models.predict import get_model_version, predict_round


def _make_team(session, name: str, home_city=None, lat=None, lon=None) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper(), home_city=home_city, home_latitude=lat, home_longitude=lon)
    session.add(team)
    session.flush()
    return team


def _make_model_version(session, name: str, final_league_avg_score=85.0) -> ModelVersion:
    version = ModelVersion(name=name, config_snapshot="{}", final_league_avg_score=final_league_avg_score)
    session.add(version)
    session.flush()
    return version


def _make_current_rating(session, team, model_version, elo=1500.0, attack=0.0, defence=0.0, form=0.0, last_match_date=None):
    session.add(CurrentTeamRating(
        team_id=team.id, model_version_id=model_version.id, elo_rating=elo,
        attack_rating=attack, defence_rating=defence, form_rating=form, last_match_date=last_match_date,
    ))


def _make_fixture(session, season_year, round_number, home, away, venue=None) -> Match:
    match = Match(
        created_by_source="squiggle", created_by_source_match_id="999",
        season_year=season_year, round_number=round_number, round_name=f"Round {round_number}",
        is_final=False, venue_id=venue.id if venue else None,
        match_date=date(season_year, 7, 1), home_team_id=home.id, away_team_id=away.id,
    )
    session.add(match)
    session.flush()
    return match


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond", "Melbourne", -37.8136, 144.9631)
    adelaide = _make_team(session, "Adelaide Crows", "Adelaide", -34.9285, 138.6007)
    venue = Venue(name="M.C.G.", city="Melbourne", latitude=-37.8199, longitude=144.9834)
    session.add(venue)
    session.flush()
    version = _make_model_version(session, "test-version")
    _make_current_rating(session, richmond, version, elo=1650.0, attack=15.0, defence=-10.0, form=0.2, last_match_date=date(2026, 6, 20))
    _make_current_rating(session, adelaide, version, elo=1500.0, attack=0.0, defence=0.0, form=0.0, last_match_date=date(2026, 6, 21))
    session.commit()
    return session, richmond, adelaide, venue, version


def test_get_model_version_returns_named_version(setup):
    session, _, _, _, version = setup
    result = get_model_version(session, "test-version")
    assert result.id == version.id


def test_get_model_version_defaults_to_latest(setup):
    session, _, _, _, version = setup
    result = get_model_version(session, version_name=None)
    assert result.id == version.id


def test_get_model_version_raises_if_none_exist(db_session):
    with pytest.raises(ValueError, match="No ratings have been computed"):
        get_model_version(db_session, version_name=None)


def test_get_model_version_raises_for_unknown_name(setup):
    session, *_ = setup
    with pytest.raises(ValueError, match="No model version named"):
        get_model_version(session, "does-not-exist")


def test_predict_round_favors_higher_rated_team(setup):
    session, richmond, adelaide, venue, version = setup
    _make_fixture(session, 2026, 22, richmond, adelaide, venue)
    session.commit()

    rows = predict_round(2026, 22, version_name="test-version")

    assert len(rows) == 1
    row = rows[0]
    assert row.home_team == "Richmond"
    assert row.away_team == "Adelaide Crows"
    assert row.predicted_winner == "Richmond"
    assert row.home_win_probability > 0.5
    assert row.predicted_margin > 0
    assert 0.0 <= row.confidence <= 100.0


def test_predict_round_persists_prediction_row(setup):
    session, richmond, adelaide, venue, version = setup
    match = _make_fixture(session, 2026, 22, richmond, adelaide, venue)
    session.commit()

    predict_round(2026, 22, version_name="test-version")

    prediction = session.execute(
        sa.select(Prediction).where(Prediction.match_id == match.id, Prediction.model_version_id == version.id)
    ).scalar_one()
    assert prediction.predicted_winner_team_id == richmond.id


def test_predict_round_is_idempotent_on_rerun(setup):
    session, richmond, adelaide, venue, version = setup
    _make_fixture(session, 2026, 22, richmond, adelaide, venue)
    session.commit()

    predict_round(2026, 22, version_name="test-version")
    predict_round(2026, 22, version_name="test-version")

    total = session.execute(sa.select(sa.func.count()).select_from(Prediction)).scalar_one()
    assert total == 1


def test_predict_round_raises_for_no_matches(setup):
    session, *_ = setup
    with pytest.raises(ValueError, match="No matches found"):
        predict_round(2026, 99, version_name="test-version")


def test_predict_round_falls_back_to_starting_rating_for_unrated_team(setup):
    session, richmond, adelaide, venue, version = setup
    expansion_team = _make_team(session, "New Expansion Team", "Darwin", -12.4, 130.8)
    _make_fixture(session, 2026, 22, richmond, expansion_team, venue)
    session.commit()

    rows = predict_round(2026, 22, version_name="test-version")

    assert len(rows) == 1
    # Should not crash, and Richmond (rated, in form) should still be favored
    # over a team resting entirely on starting values.
    assert rows[0].predicted_winner == "Richmond"
