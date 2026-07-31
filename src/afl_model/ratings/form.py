from __future__ import annotations


def update_form(previous_form: float, actual: float, expected: float, alpha: float) -> float:
    """EWMA of (actual - expected) Elo-style residuals — a shorter-memory,
    more reactive companion to the main Elo rating, meant to surface
    hot/cold streaks the slower-moving Elo rating smooths away. `actual`
    and `expected` are on the same 0-1 scale used for the Elo update
    (1 = win, 0.5 = draw, 0 = loss vs. win probability).
    """
    return alpha * (actual - expected) + (1 - alpha) * previous_form
