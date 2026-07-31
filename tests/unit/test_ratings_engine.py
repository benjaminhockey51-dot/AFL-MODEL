from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from afl_model.db.models import (
    CurrentTeamRating,
    Match,
    ModelVersion,
    Season,
    Team,
    TeamRatingHistory,
    Venue,
)
from afl_model.ratings.engine import run_ratings_engine
from afl_model.ratings.geo_reference import haversine_km


def _make_team(session, name: str, home_city=None, lat=None, lon=None) -> Team:
    team = Team(
        name=name, abbreviation=name[:3].upper(),
        home_city=home_city, home_latitude=lat, home_longitude=lon,
    )
    session.add(team)
    session.flush()
    return team


def _make_match(session, season_year, match_date, home, away, home_pts, away_pts, venue=None) -> Match:
    match = Match(
        created_by_source="test", created_by_source_match_id=f"{season_year}-{match_date}-{home.id}-{away.id}",
        season_year=season_year, round_number=1, round_name="Round 1", is_final=False,
        venue_id=venue.id if venue else None,
        match_date=match_date, home_team_id=home.id, away_team_id=away.id,
        home_points=home_pts, away_points=away_pts,
    )
    session.add(match)
    session.flush()
    return match


@pytest.fixture()
def two_teams(db_session):
    session = db_session
    session.add(Season(year=2018))
    session.add(Season(year=2019))
    richmond = _make_team(session, "Richmond", "Melbourne", -37.8136, 144.9631)
    adelaide = _make_team(session, "Adelaide Crows", "Adelaide", -34.9285, 138.6007)
    venue = Venue(name="M.C.G.", city="Melbourne", latitude=-37.8199, longitude=144.9834)
    session.add(venue)
    session.commit()
    return session, richmond, adelaide, venue


def test_history_row_reflects_state_before_the_match_not_after(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 120, 60, venue)
    _make_match(session, 2018, date(2018, 3, 29), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="test-run-1")

    rows = session.execute(
        sa.select(TeamRatingHistory)
        .join(Match, TeamRatingHistory.match_id == Match.id)
        .where(TeamRatingHistory.team_id == richmond.id)
        .order_by(Match.match_date)
    ).scalars().all()

    assert len(rows) == 2
    first_match_elo, second_match_elo = rows[0].elo_rating, rows[1].elo_rating
    # Richmond won big in match 1 — by the time match 2's snapshot is taken,
    # their rating must already reflect that win (it's higher), because the
    # snapshot recorded for match 2 is what a predictor would have known
    # *before* match 2 was played, not before match 1.
    assert second_match_elo > first_match_elo


def test_no_lookahead_first_match_uses_starting_rating(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 120, 60, venue)
    session.commit()

    run_ratings_engine(version_name="test-run-2")

    row = session.execute(
        sa.select(TeamRatingHistory).where(TeamRatingHistory.team_id == richmond.id)
    ).scalar_one()
    assert row.elo_rating == pytest.approx(1500.0)  # config default — no prior matches to move it


def test_season_boundary_regresses_rating_toward_target(two_teams):
    session, richmond, adelaide, venue = two_teams
    # Richmond dominates all of 2018 to push their rating well above 1500.
    for i in range(10):
        _make_match(session, 2018, date(2018, 3, 22) + timedelta(weeks=i), richmond, adelaide, 150, 50, venue)
    # First match of 2019.
    _make_match(session, 2019, date(2019, 3, 21), richmond, adelaide, 100, 100 - 1)
    session.commit()

    run_ratings_engine(version_name="test-run-3")

    rows = session.execute(
        sa.select(TeamRatingHistory)
        .join(Match, TeamRatingHistory.match_id == Match.id)
        .where(TeamRatingHistory.team_id == richmond.id)
        .order_by(Match.match_date)
    ).scalars().all()

    last_2018_elo = rows[9].elo_rating
    first_2019_elo = rows[10].elo_rating

    assert last_2018_elo > 1500.0
    # Regressed toward 1500, but not reset — some of the accumulated
    # advantage should carry over given season_regression_factor < 1.
    assert 1500.0 < first_2019_elo < last_2018_elo


def test_rest_adjustment_is_null_for_a_teams_first_match(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="test-run-4")

    row = session.execute(
        sa.select(TeamRatingHistory).where(TeamRatingHistory.team_id == richmond.id)
    ).scalar_one()
    assert row.rest_adjustment is None


def test_rest_adjustment_reflects_days_since_last_match(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)  # Thursday
    _make_match(session, 2018, date(2018, 3, 29), richmond, adelaide, 100, 90, venue)  # +7 days
    session.commit()

    run_ratings_engine(version_name="test-run-5")

    rows = session.execute(
        sa.select(TeamRatingHistory)
        .join(Match, TeamRatingHistory.match_id == Match.id)
        .where(TeamRatingHistory.team_id == richmond.id)
        .order_by(Match.match_date)
    ).scalars().all()
    # baseline_days=6 (config default), actual gap=7 -> +1
    assert rows[1].rest_adjustment == pytest.approx(1.0)


def test_travel_adjustment_matches_real_haversine_distance(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="test-run-6")

    row = session.execute(
        sa.select(TeamRatingHistory).where(TeamRatingHistory.team_id == adelaide.id)
    ).scalar_one()

    expected_km = haversine_km(-34.9285, 138.6007, -37.8199, 144.9834)
    assert row.travel_adjustment == pytest.approx(expected_km, rel=1e-6)

    home_row = session.execute(
        sa.select(TeamRatingHistory).where(TeamRatingHistory.team_id == richmond.id)
    ).scalar_one()
    assert home_row.travel_adjustment == pytest.approx(0.0, abs=5.0)  # Richmond is already in Melbourne


def test_travel_adjustment_is_null_when_venue_coordinates_unknown(db_session):
    session = db_session
    session.add(Season(year=2018))
    richmond = _make_team(session, "Richmond", "Melbourne", -37.8136, 144.9631)
    adelaide = _make_team(session, "Adelaide Crows", "Adelaide", -34.9285, 138.6007)
    unknown_venue = Venue(name="Some Obscure Regional Oval")  # no coordinates
    session.add(unknown_venue)
    session.flush()
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, unknown_venue)
    session.commit()

    run_ratings_engine(version_name="test-run-7")

    row = session.execute(
        sa.select(TeamRatingHistory).where(TeamRatingHistory.team_id == adelaide.id)
    ).scalar_one()
    assert row.travel_adjustment is None


def test_creates_a_new_model_version_each_run(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="run-a")
    run_ratings_engine(version_name="run-b")

    versions = session.execute(sa.select(ModelVersion.name)).scalars().all()
    assert set(versions) == {"run-a", "run-b"}

    total_history_rows = session.execute(
        sa.select(sa.func.count()).select_from(TeamRatingHistory)
    ).scalar_one()
    assert total_history_rows == 4  # 2 teams x 1 match x 2 independent runs


def test_current_team_rating_reflects_post_match_state_not_pre_match(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 120, 60, venue)
    session.commit()

    run_ratings_engine(version_name="test-current-state")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "test-current-state")
    ).scalar_one()

    pre_match_row = session.execute(
        sa.select(TeamRatingHistory).where(
            TeamRatingHistory.team_id == richmond.id,
            TeamRatingHistory.model_version_id == model_version.id,
        )
    ).scalar_one()
    current_row = session.execute(
        sa.select(CurrentTeamRating).where(
            CurrentTeamRating.team_id == richmond.id,
            CurrentTeamRating.model_version_id == model_version.id,
        )
    ).scalar_one()

    # Pre-match snapshot is the starting rating (1500) — current state must
    # already reflect Richmond's big win, i.e. be higher, not identical.
    assert pre_match_row.elo_rating == pytest.approx(1500.0)
    assert current_row.elo_rating > pre_match_row.elo_rating
    assert current_row.last_match_date == date(2018, 3, 22)
    assert current_row.last_season == 2018


def test_current_team_rating_is_one_row_per_team_per_run(two_teams):
    session, richmond, adelaide, venue = two_teams
    for i in range(3):
        _make_match(session, 2018, date(2018, 3, 22) + timedelta(weeks=i), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="test-current-state-2")

    count = session.execute(
        sa.select(sa.func.count()).select_from(CurrentTeamRating)
    ).scalar_one()
    assert count == 2  # one row per team, not one per match


def test_league_avg_score_before_reflects_pre_match_state_not_final(two_teams):
    session, richmond, adelaide, venue = two_teams
    # Very high-scoring games should drag the league average up over time.
    for i in range(5):
        _make_match(session, 2018, date(2018, 3, 22) + timedelta(weeks=i), richmond, adelaide, 200, 200, venue)
    session.commit()

    run_ratings_engine(version_name="test-league-avg")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "test-league-avg")
    ).scalar_one()

    rows = session.execute(
        sa.select(TeamRatingHistory)
        .join(Match, TeamRatingHistory.match_id == Match.id)
        .where(TeamRatingHistory.team_id == richmond.id, TeamRatingHistory.model_version_id == model_version.id)
        .order_by(Match.match_date)
    ).scalars().all()

    # First match: nothing has happened yet, so the pre-match average must
    # still be the config starting value, not dragged up by the 200-point
    # games that come later in the dataset.
    assert rows[0].league_avg_score_before == pytest.approx(85.0)
    # By the last match, the average should have climbed — but the value
    # attached to *this* match must be from before it, i.e. strictly less
    # than the run's final value (which includes this match's own update).
    assert rows[-1].league_avg_score_before > rows[0].league_avg_score_before
    assert rows[-1].league_avg_score_before < model_version.final_league_avg_score
