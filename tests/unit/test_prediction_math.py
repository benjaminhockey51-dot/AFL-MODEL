from __future__ import annotations

import pytest

from afl_model.models.prediction_math import PredictionConfig, TeamPredictionInputs, compute_prediction

CONFIG = PredictionConfig(
    form_elo_scale=100.0, rest_elo_scale_per_day=3.0, travel_elo_scale_per_100km=1.5,
    line_rounding=0.5, confidence_maturity_games=22,
)


def _team(elo=1500.0, attack=0.0, defence=0.0, form=0.0, rest_days=0.0, travel_km=0.0, games_played=22):
    return TeamPredictionInputs(
        elo=elo, attack=attack, defence=defence, form=form,
        rest_days=rest_days, travel_km=travel_km, games_played=games_played,
    )


def test_equal_teams_at_neutral_venue_gives_home_ground_edge_only():
    result = compute_prediction(_team(), _team(), home_ground_advantage=35.0, league_avg_score=85.0, config=CONFIG)
    assert result.home_win_probability > 0.5
    assert result.predicted_margin == pytest.approx(0.0)
    assert result.predicted_total == pytest.approx(170.0)
    assert result.predicted_winner == "home"


def test_stronger_attack_and_weaker_opponent_defence_increases_margin():
    home = _team(attack=20.0)
    away = _team(defence=10.0)  # worse defence (concedes more)
    result = compute_prediction(home, away, home_ground_advantage=0.0, league_avg_score=85.0, config=CONFIG)
    assert result.predicted_margin > 0
    assert result.predicted_winner == "home"


def test_better_form_increases_home_win_probability():
    baseline = compute_prediction(_team(), _team(), 0.0, 85.0, CONFIG)
    home_in_form = compute_prediction(_team(form=0.5), _team(), 0.0, 85.0, CONFIG)
    assert home_in_form.home_win_probability > baseline.home_win_probability


def test_extra_rest_for_home_increases_win_probability():
    baseline = compute_prediction(_team(), _team(), 0.0, 85.0, CONFIG)
    home_rested = compute_prediction(_team(rest_days=3.0), _team(rest_days=-3.0), 0.0, 85.0, CONFIG)
    assert home_rested.home_win_probability > baseline.home_win_probability


def test_more_travel_for_home_decreases_win_probability():
    baseline = compute_prediction(_team(), _team(), 0.0, 85.0, CONFIG)
    home_travelled = compute_prediction(_team(travel_km=1000.0), _team(travel_km=0.0), 0.0, 85.0, CONFIG)
    assert home_travelled.home_win_probability < baseline.home_win_probability


def test_missing_rest_and_travel_treated_as_neutral_not_crashing():
    home = _team(rest_days=None, travel_km=None)
    away = _team(rest_days=None, travel_km=None)
    result = compute_prediction(home, away, 35.0, 85.0, CONFIG)
    assert 0.0 < result.home_win_probability < 1.0


def test_predicted_line_rounds_to_nearest_half_point():
    home = _team(attack=13.3)
    result = compute_prediction(home, _team(), 0.0, 85.0, CONFIG)
    assert result.predicted_margin == pytest.approx(13.3)
    assert result.predicted_line == pytest.approx(13.5)


def test_confidence_is_zero_for_a_coin_flip():
    result = compute_prediction(_team(), _team(), home_ground_advantage=0.0, league_avg_score=85.0, config=CONFIG)
    assert result.confidence == pytest.approx(0.0)


def test_confidence_increases_with_win_probability_distance_from_fifty_percent():
    close = compute_prediction(_team(elo=1520), _team(elo=1500), 0.0, 85.0, CONFIG)
    lopsided = compute_prediction(_team(elo=1800), _team(elo=1200), 0.0, 85.0, CONFIG)
    assert lopsided.confidence > close.confidence


def test_confidence_is_discounted_for_unraced_teams():
    mature = compute_prediction(
        _team(elo=1800, games_played=30), _team(elo=1200, games_played=30), 0.0, 85.0, CONFIG,
    )
    unraced = compute_prediction(
        _team(elo=1800, games_played=0), _team(elo=1200, games_played=0), 0.0, 85.0, CONFIG,
    )
    assert unraced.confidence < mature.confidence
    assert unraced.confidence == pytest.approx(0.0)


def test_predicted_winner_tracks_win_probability_not_margin_sign():
    # Regression test: predicted_winner must follow home_win_probability
    # (Elo-based) even when predicted_margin (attack/defence-based) points
    # the other way — the two are independently-fit signals allowed to
    # disagree, but a single prediction can't say both "away is favoured to
    # win" and "we predict home wins."
    home = _team(elo=1900.0, attack=-30.0, defence=0.0)
    away = _team(elo=1100.0, attack=0.0, defence=-30.0)
    result = compute_prediction(home, away, home_ground_advantage=0.0, league_avg_score=85.0, config=CONFIG)

    assert result.home_win_probability > 0.5
    assert result.predicted_margin < 0  # attack/defence alone would favour away
    assert result.predicted_winner == "home"  # but the winner call follows probability


def test_confidence_never_exceeds_100():
    result = compute_prediction(
        _team(elo=2500, games_played=100), _team(elo=500, games_played=100), 35.0, 85.0, CONFIG,
    )
    assert result.confidence <= 100.0
