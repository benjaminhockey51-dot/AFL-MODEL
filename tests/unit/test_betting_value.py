from __future__ import annotations

import pytest

from afl_model.betting.value import (
    OddsQuote,
    assess_value,
    best_value_across_quotes,
    expected_value,
    remove_overround,
)


def test_remove_overround_sums_to_one():
    fair_home, fair_away = remove_overround(1.90, 1.90)
    assert fair_home + fair_away == pytest.approx(1.0)
    assert fair_home == pytest.approx(0.5)


def test_remove_overround_reflects_relative_favouritism():
    fair_home, fair_away = remove_overround(1.50, 2.70)
    assert fair_home > fair_away


def test_expected_value_positive_when_probability_exceeds_implied():
    # Fair coin (50%) at 2.10 odds (implied ~47.6%) is a positive-EV bet.
    assert expected_value(0.5, 2.10) > 0


def test_expected_value_negative_when_probability_below_implied():
    assert expected_value(0.4, 2.00) < 0  # implied 50%, we think 40%


def test_expected_value_zero_at_fair_price():
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)


def test_assess_value_no_bet_when_threshold_not_configured():
    # Even with a huge apparent edge, an unconfigured threshold (None) must
    # never produce a recommendation — matches "never guess a threshold."
    odds = OddsQuote(bookmaker="TestBook", home_decimal_odds=3.00, away_decimal_odds=1.40)
    result = assess_value(our_home_probability=0.6, odds=odds, min_edge_threshold=None)
    assert result.recommendation == "No Bet"


def test_assess_value_recommends_home_when_edge_and_ev_both_clear():
    # Bookmaker prices imply ~33% home / ~71% away (with overround); we
    # think home is a 60% chance — a real, large edge.
    odds = OddsQuote(bookmaker="TestBook", home_decimal_odds=3.00, away_decimal_odds=1.40)
    result = assess_value(our_home_probability=0.6, odds=odds, min_edge_threshold=0.05)
    assert result.recommendation == "Bet Home"
    assert result.home_edge > 0.05
    assert result.home_ev > 0


def test_assess_value_no_bet_when_edge_below_threshold():
    odds = OddsQuote(bookmaker="TestBook", home_decimal_odds=2.00, away_decimal_odds=1.90)
    result = assess_value(our_home_probability=0.51, odds=odds, min_edge_threshold=0.10)
    assert result.recommendation == "No Bet"


def test_assess_value_never_recommends_both_sides():
    odds = OddsQuote(bookmaker="TestBook", home_decimal_odds=2.00, away_decimal_odds=1.90)
    result = assess_value(our_home_probability=0.55, odds=odds, min_edge_threshold=0.01)
    assert result.recommendation in ("Bet Home", "Bet Away", "No Bet")
    # Can't simultaneously be a value bet on both sides of the same market.
    assert not (result.home_edge > 0.01 and result.away_edge > 0.01)


def test_best_value_across_quotes_returns_none_for_empty_list():
    assert best_value_across_quotes(0.6, [], min_edge_threshold=0.05) is None


def test_best_value_across_quotes_picks_the_better_price():
    stingy = OddsQuote(bookmaker="Stingy", home_decimal_odds=1.60, away_decimal_odds=2.60)
    generous = OddsQuote(bookmaker="Generous", home_decimal_odds=2.20, away_decimal_odds=1.80)
    result = best_value_across_quotes(0.6, [stingy, generous], min_edge_threshold=0.05)
    assert result.bookmaker == "Generous"
    assert result.recommendation == "Bet Home"


def test_best_value_across_quotes_falls_back_to_no_bet_when_none_qualify():
    fair1 = OddsQuote(bookmaker="A", home_decimal_odds=1.98, away_decimal_odds=1.98)
    fair2 = OddsQuote(bookmaker="B", home_decimal_odds=1.99, away_decimal_odds=1.99)
    result = best_value_across_quotes(0.505, [fair1, fair2], min_edge_threshold=0.10)
    assert result.recommendation == "No Bet"
