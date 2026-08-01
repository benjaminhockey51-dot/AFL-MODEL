from __future__ import annotations

from datetime import date

import pytest

from afl_model.db.models import CurrentTeamRating, Match, ModelVersion, Odds, Season, Team, Venue
from afl_model.reporting.round_report import build_round_report


def _make_team(session, name: str, home_city=None, lat=None, lon=None) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper(), home_city=home_city, home_latitude=lat, home_longitude=lon)
    session.add(team)
    session.flush()
    return team


def _make_model_version(session, name: str) -> ModelVersion:
    version = ModelVersion(name=name, config_snapshot="{}", final_league_avg_score=85.0)
    session.add(version)
    session.flush()
    return version


def _make_current_rating(session, team, model_version, elo, attack=0.0, defence=0.0):
    session.add(CurrentTeamRating(
        team_id=team.id, model_version_id=model_version.id, elo_rating=elo,
        attack_rating=attack, defence_rating=defence, form_rating=0.0,
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
    strong = _make_team(session, "Richmond", "Melbourne", -37.8136, 144.9631)
    weak = _make_team(session, "Adelaide Crows", "Adelaide", -34.9285, 138.6007)
    venue = Venue(name="M.C.G.", city="Melbourne", latitude=-37.8199, longitude=144.9834)
    session.add(venue)
    session.flush()
    version = _make_model_version(session, "test-version")
    _make_current_rating(session, strong, version, elo=1700.0, attack=20.0, defence=-10.0)
    _make_current_rating(session, weak, version, elo=1500.0, attack=0.0, defence=0.0)
    session.commit()
    return session, strong, weak, venue, version


def test_build_round_report_produces_a_row_per_match(setup):
    session, strong, weak, venue, version = setup
    _make_fixture(session, 2026, 22, strong, weak, venue)
    session.commit()

    report = build_round_report(2026, 22, version_name="test-version")

    assert len(report.matches) == 1
    row = report.matches[0]
    assert row.home_team == "Richmond"
    assert row.predicted_winner == "Richmond"
    assert row.explanation  # non-empty
    assert "Richmond" in row.explanation


def test_build_round_report_highest_confidence_sorted_descending(setup):
    session, strong, weak, venue, version = setup
    other_team = _make_team(session, "Geelong Cats", "Geelong", -38.1499, 144.3617)
    _make_current_rating(session, other_team, version, elo=1505.0)
    session.flush()
    _make_fixture(session, 2026, 22, strong, weak, venue)  # big mismatch -> high confidence
    _make_fixture(session, 2026, 23, weak, other_team, venue)  # close matchup -> low confidence
    session.commit()

    report = build_round_report(2026, 22, version_name="test-version")
    report23 = build_round_report(2026, 23, version_name="test-version")

    assert report.matches[0].confidence >= report23.matches[0].confidence


def test_build_round_report_games_to_avoid_for_close_matchups(setup):
    session, strong, weak, venue, version = setup
    close_opponent = _make_team(session, "Geelong Cats", "Geelong", -38.1499, 144.3617)
    _make_current_rating(session, close_opponent, version, elo=1501.0)
    session.flush()
    _make_fixture(session, 2026, 22, weak, close_opponent, venue)
    session.commit()

    report = build_round_report(2026, 22, version_name="test-version")

    assert len(report.games_to_avoid) == 1
    assert report.games_to_avoid[0].confidence < 20.0


def test_build_round_report_best_value_empty_without_odds(setup):
    session, strong, weak, venue, version = setup
    _make_fixture(session, 2026, 22, strong, weak, venue)
    session.commit()

    report = build_round_report(2026, 22, version_name="test-version")

    assert report.best_value == []


def test_build_round_report_best_value_populated_with_real_edge(setup, monkeypatch):
    import afl_model.betting.recommend as recommend_module
    from afl_model.betting.config import BettingConfig

    session, strong, weak, venue, version = setup
    match = _make_fixture(session, 2026, 22, strong, weak, venue)
    session.add(Odds(
        match_id=match.id, bookmaker="TestBook", snapshot_type="close",
        home_decimal_odds=3.00, away_decimal_odds=1.40, source="fakeodds",
    ))
    session.commit()

    monkeypatch.setattr(
        recommend_module, "load_betting_config",
        lambda: BettingConfig(odds_source=None, min_edge_threshold=0.05),
    )
    report = build_round_report(2026, 22, version_name="test-version")

    assert len(report.best_value) == 1
    assert report.best_value[0].recommendation == "Bet Home"
