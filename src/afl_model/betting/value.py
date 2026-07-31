from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class OddsQuote:
    bookmaker: str
    home_decimal_odds: float
    away_decimal_odds: float


@dataclass(frozen=True)
class ValueAssessment:
    bookmaker: str
    fair_home_probability: float  # this bookmaker's own two-way price, overround removed
    fair_away_probability: float
    home_edge: float  # our probability minus the fair (de-vigged) probability
    away_edge: float
    home_ev: float  # expected value per $1 staked, at this bookmaker's actual (non-de-vigged) price
    away_ev: float
    recommendation: str  # "Bet Home" | "Bet Away" | "No Bet"


def remove_overround(home_decimal_odds: float, away_decimal_odds: float) -> "tuple[float, float]":
    """Bookmaker prices always imply a combined probability over 100% (their
    margin/vig) — this rescales both sides so they sum to exactly 1,
    giving the bookmaker's actual assessed probability rather than the
    inflated one their prices show at face value.
    """
    home_implied = 1.0 / home_decimal_odds
    away_implied = 1.0 / away_decimal_odds
    total = home_implied + away_implied
    return home_implied / total, away_implied / total


def expected_value(probability: float, decimal_odds: float) -> float:
    """Expected profit per $1 staked, at face-value (non-de-vigged) odds —
    this is what actually gets paid out, so it's what a bet's real
    expected return is measured against, not the fair/de-vigged price.
    """
    return probability * decimal_odds - 1.0


def assess_value(
    our_home_probability: float, odds: OddsQuote, min_edge_threshold: Optional[float],
) -> ValueAssessment:
    """Compares our model's probability against one bookmaker's price.

    A bet is only ever recommended when min_edge_threshold is an actual
    configured number (never a guessed default — see config.yaml) AND both
    the de-vigged edge and the raw expected value clear it; "No Bet" is
    the default outcome, not the exception, matching the project's rule
    that a recommendation must never be made without genuine value.
    """
    fair_home, fair_away = remove_overround(odds.home_decimal_odds, odds.away_decimal_odds)
    our_away_probability = 1.0 - our_home_probability

    home_edge = our_home_probability - fair_home
    away_edge = our_away_probability - fair_away
    home_ev = expected_value(our_home_probability, odds.home_decimal_odds)
    away_ev = expected_value(our_away_probability, odds.away_decimal_odds)

    recommendation = "No Bet"
    if min_edge_threshold is not None:
        if home_edge >= min_edge_threshold and home_ev > 0:
            recommendation = "Bet Home"
        elif away_edge >= min_edge_threshold and away_ev > 0:
            recommendation = "Bet Away"

    return ValueAssessment(
        bookmaker=odds.bookmaker, fair_home_probability=fair_home, fair_away_probability=fair_away,
        home_edge=home_edge, away_edge=away_edge, home_ev=home_ev, away_ev=away_ev,
        recommendation=recommendation,
    )


def best_value_across_quotes(
    our_home_probability: float, quotes: List[OddsQuote], min_edge_threshold: Optional[float],
) -> Optional[ValueAssessment]:
    """When multiple bookmakers have priced the same match, a real bettor
    always shops for the best price — this evaluates every quote and
    returns whichever gives the strongest recommendation (by expected
    value, among quotes that clear the edge threshold), or the first
    quote's "No Bet" assessment if none do. Returns None only if there
    are no quotes at all.
    """
    if not quotes:
        return None

    assessments = [assess_value(our_home_probability, q, min_edge_threshold) for q in quotes]
    value_bets = [a for a in assessments if a.recommendation != "No Bet"]
    if not value_bets:
        return assessments[0]

    return max(value_bets, key=lambda a: a.home_ev if a.recommendation == "Bet Home" else a.away_ev)
