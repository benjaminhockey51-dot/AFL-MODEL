from __future__ import annotations

import math

import pytest

from afl_model.ratings.elo import (
    EloConfig,
    apply_season_regression,
    expected_home_win_probability,
    margin_of_victory_multiplier,
    update_ratings,
)

CONFIG = EloConfig(
    starting_rating=1500.0, k_factor=20.0, mov_multiplier_divisor=2.2,
    mov_multiplier_scale=0.001, home_ground_advantage=35.0,
    season_regression_factor=0.75, season_regression_target=1500.0,
)


def test_equal_ratings_no_hga_gives_fifty_fifty():
    assert expected_home_win_probability(1500, 1500, home_ground_advantage=0) == pytest.approx(0.5)


def test_home_ground_advantage_favors_home_team_at_equal_ratings():
    prob = expected_home_win_probability(1500, 1500, home_ground_advantage=35.0)
    assert prob > 0.5


def test_higher_rating_increases_win_probability():
    prob = expected_home_win_probability(1700, 1500, home_ground_advantage=0)
    assert prob > 0.5


def test_update_ratings_is_zero_sum():
    new_home, new_away = update_ratings(1500, 1500, 100, 80, CONFIG)
    assert (new_home - 1500) == pytest.approx(-(new_away - 1500))


def test_winning_team_rating_increases():
    new_home, new_away = update_ratings(1500, 1500, 100, 80, CONFIG)
    assert new_home > 1500
    assert new_away < 1500


def test_upset_win_moves_rating_more_than_expected_win():
    # Underdog (away, much lower rated) wins — should move ratings a lot.
    upset_home, upset_away = update_ratings(1400, 1700, 70, 100, CONFIG)
    upset_delta = abs(upset_away - 1700)

    # Same margin, but the higher-rated team was already expected to win.
    expected_home, expected_away = update_ratings(1700, 1400, 100, 70, CONFIG)
    expected_delta = abs(expected_home - 1700)

    assert upset_delta > expected_delta


def test_draw_still_moves_ratings_when_one_side_was_favored():
    # Regression test: naively applying ln(margin+1) with margin=0 zeroes
    # out all rating movement on a draw, silently ignoring how surprising
    # it was. A big favorite drawing should still lose rating.
    new_home, new_away = update_ratings(1700, 1400, 80, 80, CONFIG)
    assert new_home < 1700
    assert new_away > 1400


def test_margin_of_victory_multiplier_damps_expected_blowout_vs_upset():
    expected_blowout = margin_of_victory_multiplier(margin=60, winner_rating_edge=300, divisor=2.2, scale=0.001)
    upset_same_margin = margin_of_victory_multiplier(margin=60, winner_rating_edge=0, divisor=2.2, scale=0.001)
    assert upset_same_margin > expected_blowout


def test_season_regression_pulls_toward_target():
    high_rating = apply_season_regression(1700.0, CONFIG)
    assert 1500.0 < high_rating < 1700.0

    low_rating = apply_season_regression(1300.0, CONFIG)
    assert 1300.0 < low_rating < 1500.0


def test_season_regression_leaves_target_rating_unchanged():
    assert apply_season_regression(1500.0, CONFIG) == pytest.approx(1500.0)


def test_margin_of_victory_multiplier_is_never_negative_or_nan():
    result = margin_of_victory_multiplier(margin=1, winner_rating_edge=-500, divisor=2.2, scale=0.001)
    assert result > 0
    assert not math.isnan(result)
