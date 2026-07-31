from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from afl_model.backtest.dataset import load_backtest_dataset, load_home_ground_advantage
from afl_model.db.models import Match, ModelVersion, Season, Team, Venue
from afl_model.ratings.engine import run_ratings_engine


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _make_match(session, season_year, match_date, home, away, home_pts, away_pts, venue=None) -> Match:
    match = Match(
        created_by_source="test", created_by_source_match_id=f"{season_year}-{match_date}-{home.id}-{away.id}",
        season_year=season_year, round_number=1, round_name="Round 1", is_final=False,
        venue_id=venue.id if venue else None, match_date=match_date,
        home_team_id=home.id, away_team_id=away.id, home_points=home_pts, away_points=away_pts,
    )
    session.add(match)
    session.flush()
    return match


@pytest.fixture()
def two_teams(db_session):
    session = db_session
    session.add(Season(year=2018))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    venue = Venue(name="M.C.G.")
    session.add(venue)
    session.commit()
    return session, richmond, adelaide, venue


def test_load_backtest_dataset_orders_chronologically_and_counts_games_played(two_teams):
    session, richmond, adelaide, venue = two_teams
    for i in range(3):
        _make_match(session, 2018, date(2018, 3, 22) + timedelta(weeks=i), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="dataset-test-1")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "dataset-test-1")
    ).scalar_one()

    dataset = load_backtest_dataset(session, model_version.id)

    assert len(dataset) == 3
    assert [m.home_points for m in dataset] == [100, 100, 100]
    # games_played must reflect matches BEFORE this one, not including it.
    assert [m.home.games_played for m in dataset] == [0, 1, 2]
    assert [m.away.games_played for m in dataset] == [0, 1, 2]


def test_load_backtest_dataset_excludes_unplayed_matches(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)
    unplayed = Match(
        created_by_source="test", created_by_source_match_id="unplayed",
        season_year=2018, round_number=2, round_name="Round 2", is_final=False,
        venue_id=venue.id, match_date=date(2018, 3, 29),
        home_team_id=richmond.id, away_team_id=adelaide.id, home_points=None, away_points=None,
    )
    session.add(unplayed)
    session.commit()

    run_ratings_engine(version_name="dataset-test-2")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "dataset-test-2")
    ).scalar_one()

    dataset = load_backtest_dataset(session, model_version.id)
    assert len(dataset) == 1


def test_load_backtest_dataset_uses_pre_match_league_avg_not_final(two_teams):
    session, richmond, adelaide, venue = two_teams
    for i in range(5):
        _make_match(session, 2018, date(2018, 3, 22) + timedelta(weeks=i), richmond, adelaide, 200, 200, venue)
    session.commit()

    run_ratings_engine(version_name="dataset-test-3")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "dataset-test-3")
    ).scalar_one()

    dataset = load_backtest_dataset(session, model_version.id)
    assert dataset[0].league_avg_score == pytest.approx(85.0)
    assert dataset[-1].league_avg_score < model_version.final_league_avg_score


def test_load_home_ground_advantage_reads_from_config_snapshot(two_teams):
    session, richmond, adelaide, venue = two_teams
    _make_match(session, 2018, date(2018, 3, 22), richmond, adelaide, 100, 90, venue)
    session.commit()

    run_ratings_engine(version_name="dataset-test-4")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "dataset-test-4")
    ).scalar_one()

    hga = load_home_ground_advantage(model_version)
    assert hga == pytest.approx(35.0)  # config.yaml default
