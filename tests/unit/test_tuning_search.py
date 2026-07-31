from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

import afl_model.tuning.grid as grid
from afl_model.db.models import Match, Season, Team, Venue
from afl_model.tuning.search import run_full_search, search_margin_params, search_win_probability_params


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
def small_grids(monkeypatch):
    """Shrinks every grid to 2 values so the search runs in milliseconds
    instead of minutes, while still exercising the real search logic (two
    outer x two inner x two margin combinations, not a degenerate single
    point).
    """
    monkeypatch.setattr(grid, "ELO_K_FACTOR_GRID", [15.0, 25.0])
    monkeypatch.setattr(grid, "HOME_GROUND_ADVANTAGE_GRID", [20.0, 40.0])
    monkeypatch.setattr(grid, "SEASON_REGRESSION_FACTOR_GRID", [0.6, 0.9])
    monkeypatch.setattr(grid, "FORM_ELO_SCALE_GRID", [0.0, 50.0])
    monkeypatch.setattr(grid, "REST_ELO_SCALE_PER_DAY_GRID", [0.0, 2.0])
    monkeypatch.setattr(grid, "TRAVEL_ELO_SCALE_PER_100KM_GRID", [0.0, 1.0])
    monkeypatch.setattr(grid, "ATTACK_DEFENCE_K_FACTOR_GRID", [0.10, 0.20])
    monkeypatch.setattr(grid, "LEAGUE_AVG_SCORE_EWMA_ALPHA_GRID", [0.02, 0.05])


@pytest.fixture()
def spanning_dataset(db_session):
    """Matches spread across a warmup season (2018), several validation
    seasons (2019-2024), and held-out test seasons (2025-2026) — enough in
    each bucket for the metric functions to produce non-degenerate results.
    """
    session = db_session
    for year in [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]:
        session.add(Season(year=year))
    strong = _make_team(session, "Strong Team", "Melbourne", -37.8136, 144.9631)
    weak = _make_team(session, "Weak Team", "Sydney", -33.8688, 151.2093)
    venue = Venue(name="M.C.G.", city="Melbourne", latitude=-37.8199, longitude=144.9834)
    session.add(venue)
    session.flush()

    for year in [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]:
        match_date = date(year, 3, 22)
        for i in range(8):
            home, away = (strong, weak) if i % 2 == 0 else (weak, strong)
            home_pts, away_pts = (120, 80) if home is strong else (80, 120)
            _make_match(session, year, match_date + timedelta(weeks=i), home, away, home_pts, away_pts, venue)
    session.commit()
    return session


def test_search_win_probability_params_returns_all_combinations(small_grids, spanning_dataset):
    session = spanning_dataset
    from afl_model.db.models import Team as TeamModel
    from afl_model.ratings.config import load_ratings_config
    from afl_model.models.config import load_prediction_config

    teams_by_id = {t.id: t for t in session.execute(sa.select(TeamModel)).scalars().all()}
    venues_by_id = {v.id: v for v in session.execute(sa.select(Venue)).scalars().all()}
    matches = session.execute(sa.select(Match).order_by(Match.match_date)).scalars().all()

    candidates = search_win_probability_params(
        matches, teams_by_id, venues_by_id, load_ratings_config(), load_prediction_config(),
    )
    # 2 (k_factor) x 2 (hga) x 2 (regression) x 2 (form) x 2 (rest) x 2 (travel)
    assert len(candidates) == 64
    assert all(c.n > 0 for c in candidates)


def test_search_margin_params_returns_all_combinations(small_grids, spanning_dataset):
    session = spanning_dataset
    from afl_model.db.models import Team as TeamModel
    from afl_model.ratings.config import load_ratings_config
    from afl_model.models.config import load_prediction_config

    teams_by_id = {t.id: t for t in session.execute(sa.select(TeamModel)).scalars().all()}
    venues_by_id = {v.id: v for v in session.execute(sa.select(Venue)).scalars().all()}
    matches = session.execute(sa.select(Match).order_by(Match.match_date)).scalars().all()

    candidates = search_margin_params(
        matches, teams_by_id, venues_by_id, load_ratings_config(), load_prediction_config(),
    )
    assert len(candidates) == 4  # 2 x 2
    assert all(c.n > 0 for c in candidates)


def test_run_full_search_freezes_the_best_candidates(small_grids, spanning_dataset):
    result = run_full_search(spanning_dataset)

    assert result.frozen_ratings_config.elo.k_factor == result.best_win_probability.k_factor
    assert result.frozen_ratings_config.elo.home_ground_advantage == result.best_win_probability.home_ground_advantage
    assert result.frozen_prediction_config.form_elo_scale == result.best_win_probability.form_elo_scale
    assert result.frozen_ratings_config.attack_defence.k_factor == result.best_margin.attack_defence_k_factor

    # Best-by-definition: no other candidate should have a strictly lower log loss / MAE.
    assert all(c.log_loss >= result.best_win_probability.log_loss for c in result.win_probability_candidates)
    assert all(c.margin_mae >= result.best_margin.margin_mae for c in result.margin_candidates)


def test_run_full_search_test_metrics_only_use_test_seasons(small_grids, spanning_dataset):
    result = run_full_search(spanning_dataset)
    # 2 test seasons x 8 matches = 16 matches total in the test set.
    assert result.test_win_probability["n"] == 16
    assert result.test_margin["n"] == 16
    # 6 validation seasons x 8 matches = 48.
    assert result.validation_win_probability["n"] == 48
    assert result.validation_margin["n"] == 48
