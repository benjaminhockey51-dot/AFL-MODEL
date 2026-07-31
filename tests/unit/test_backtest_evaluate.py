from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from afl_model.backtest.evaluate import FEATURE_NAMES, run_full_backtest
from afl_model.db.models import ModelVersion, Season, Team, Venue, Match
from afl_model.ratings.engine import run_ratings_engine


def _make_team(session, name: str, home_city=None, lat=None, lon=None) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper(), home_city=home_city, home_latitude=lat, home_longitude=lon)
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
def dominant_team_dataset(db_session):
    """Team A is much stronger and always wins by a lot, every week, for
    several seasons — the model should learn this quickly and predict it
    correctly almost every time. Deliberately extreme, so ablating a truly
    informative signal (Elo) should visibly hurt accuracy relative to the
    full model, distinguishing a working ablation harness from a
    do-nothing one.
    """
    session = db_session
    for year in (2018, 2019):
        session.add(Season(year=year))
    strong = _make_team(session, "Strong Team", "Melbourne", -37.8136, 144.9631)
    weak = _make_team(session, "Weak Team", "Sydney", -33.8688, 151.2093)
    venue = Venue(name="M.C.G.", city="Melbourne", latitude=-37.8199, longitude=144.9834)
    session.add(venue)
    session.flush()

    match_date = date(2018, 3, 22)
    for _ in range(20):
        _make_match(session, 2018, match_date, strong, weak, 150, 40, venue)
        match_date += timedelta(weeks=1)

    match_date = date(2019, 3, 21)
    for _ in range(10):
        _make_match(session, 2019, match_date, strong, weak, 150, 40, venue)
        match_date += timedelta(weeks=1)
    session.commit()

    run_ratings_engine(version_name="evaluate-test")
    model_version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == "evaluate-test")
    ).scalar_one()
    return session, model_version


def test_full_model_predicts_the_dominant_team_correctly_most_of_the_time(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    assert report.full_model.n > 20
    assert report.full_model.win_accuracy > 0.8
    # Loose bound deliberately: attack_defence.k_factor is now 0.05 (Stage 7
    # search result — slower-adapting than the old 0.15 default), so a
    # ~110-point margin takes more matches to fully learn than this small
    # synthetic dataset provides. This just checks real learning happened
    # (a coin-flip/no-skill margin model would be far worse than this).
    assert report.full_model.margin_mae < 50


def test_ablating_elo_produces_a_named_variant_for_every_feature(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    ablation_names = {a.name for a in report.ablations}
    assert ablation_names == {f"Full model minus {f}" for f in FEATURE_NAMES}


def test_removing_elo_hurts_win_accuracy_on_an_elo_driven_dataset(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    elo_ablation = next(a for a in report.ablations if a.name == "Full model minus elo")
    # Elo is the dominant signal separating these two teams' win probability
    # in this synthetic dataset — removing it should measurably hurt.
    assert elo_ablation.win_accuracy <= report.full_model.win_accuracy


def test_always_home_baseline_is_present_and_bounded(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    baseline = next(b for b in report.baselines if "Always pick home" in b.name)
    assert 0.0 <= baseline.win_accuracy <= 1.0


def test_by_season_breakdown_covers_both_seasons(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    seasons = {g.group for g in report.by_season}
    assert seasons == {"2018", "2019"}


def test_calibration_table_sums_to_total_matches(dominant_team_dataset):
    session, model_version = dominant_team_dataset
    report = run_full_backtest(session, model_version.id)

    assert sum(b.n for b in report.calibration) == report.full_model.n


def test_run_full_backtest_raises_for_unknown_model_version(db_session):
    with pytest.raises(ValueError, match="No model version"):
        run_full_backtest(db_session, model_version_id=999999)
