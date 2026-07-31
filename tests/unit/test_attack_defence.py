from __future__ import annotations

import pytest

from afl_model.ratings.attack_defence import AttackDefenceConfig, expected_scores, update_attack_defence

CONFIG = AttackDefenceConfig(k_factor=0.15, starting_league_avg_score=85.0, league_avg_score_ewma_alpha=0.02)


def test_expected_scores_at_neutral_ratings_equal_league_average():
    home, away = expected_scores(0, 0, 0, 0, league_avg_score=85.0)
    assert home == pytest.approx(85.0)
    assert away == pytest.approx(85.0)


def test_strong_attack_raises_expected_score():
    home, _ = expected_scores(home_attack=20, home_defence=0, away_attack=0, away_defence=0, league_avg_score=85.0)
    assert home == pytest.approx(105.0)


def test_weak_opponent_defence_raises_expected_score():
    home, _ = expected_scores(home_attack=0, home_defence=0, away_attack=0, away_defence=15, league_avg_score=85.0)
    assert home == pytest.approx(100.0)


def test_scoring_more_than_expected_raises_attack_and_opponent_defence():
    result = update_attack_defence(
        home_attack=0, home_defence=0, away_attack=0, away_defence=0,
        league_avg_score=85.0, home_score=120, away_score=85, config=CONFIG,
    )
    assert result.home_attack > 0  # scored well above expectation
    assert result.away_defence > 0  # conceded well above expectation ("worse" defence)


def test_scoring_less_than_expected_lowers_attack_and_opponent_defence_stays_neutral():
    result = update_attack_defence(
        home_attack=0, home_defence=0, away_attack=0, away_defence=0,
        league_avg_score=85.0, home_score=50, away_score=85, config=CONFIG,
    )
    assert result.home_attack < 0
    assert result.away_defence < 0  # conceded well below expectation ("better" defence)


def test_league_avg_score_drifts_toward_actual_scoring():
    result = update_attack_defence(
        home_attack=0, home_defence=0, away_attack=0, away_defence=0,
        league_avg_score=85.0, home_score=150, away_score=150, config=CONFIG,
    )
    assert result.league_avg_score > 85.0
    assert result.league_avg_score < 150.0  # EWMA, not a jump straight to the new value
