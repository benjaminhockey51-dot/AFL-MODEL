from __future__ import annotations

from afl_model.reporting.explain import ExplanationInputs, explain_prediction


def _inputs(**overrides):
    defaults = dict(
        home_team="Richmond", away_team="Adelaide Crows", predicted_winner="home",
        home_win_probability=0.7, predicted_margin=20.0,
        home_elo=1600.0, away_elo=1500.0, home_attack=10.0, away_attack=0.0,
        home_defence=0.0, away_defence=0.0,
        home_travel_km=0.0, away_travel_km=0.0, travel_elo_scale_per_100km=2.5,
        confidence=70.0,
    )
    defaults.update(overrides)
    return ExplanationInputs(**defaults)


def test_explain_mentions_the_predicted_winner_and_probability():
    text = explain_prediction(_inputs())
    assert "Richmond" in text
    assert "70%" in text


def test_explain_uses_away_win_probability_when_away_favoured():
    text = explain_prediction(_inputs(predicted_winner="away", home_win_probability=0.3, predicted_margin=-15.0))
    assert "Adelaide Crows" in text
    assert "70%" in text  # away win prob = 1 - 0.3


def test_explain_mentions_elo_edge_when_meaningful():
    text = explain_prediction(_inputs(home_elo=1650.0, away_elo=1450.0))
    assert "Elo rating edge of 200" in text
    assert "Richmond" in text.split("Elo")[1]


def test_explain_omits_elo_when_negligible():
    text = explain_prediction(_inputs(home_elo=1500.2, away_elo=1500.0))
    assert "Elo rating edge" not in text


def test_explain_mentions_attack_defence_edge():
    text = explain_prediction(_inputs(home_attack=15.0, away_attack=0.0, home_defence=0.0, away_defence=5.0))
    assert "Attack/defence" in text


def test_explain_never_mentions_travel_when_scale_is_zero():
    text = explain_prediction(_inputs(
        home_travel_km=3000.0, away_travel_km=0.0, travel_elo_scale_per_100km=0.0,
    ))
    assert "travelled" not in text


def test_explain_omits_travel_when_difference_is_small():
    text = explain_prediction(_inputs(home_travel_km=100.0, away_travel_km=50.0, travel_elo_scale_per_100km=2.5))
    assert "travelled" not in text


def test_explain_mentions_travel_when_difference_is_large_and_scale_nonzero():
    text = explain_prediction(_inputs(home_travel_km=3000.0, away_travel_km=0.0, travel_elo_scale_per_100km=2.5))
    assert "Richmond has travelled 3000km further" in text


def test_explain_handles_missing_travel_data_gracefully():
    text = explain_prediction(_inputs(home_travel_km=None, away_travel_km=None))
    assert "travelled" not in text
