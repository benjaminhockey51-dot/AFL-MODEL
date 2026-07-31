from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, TypeVar

T = TypeVar("T")

_EPSILON = 1e-15


def win_accuracy(predicted_home_win: Sequence[bool], actual_outcome: Sequence[float]) -> "tuple[float, int]":
    """Fraction of *decisive* matches (excludes real draws — there's no
    correct pick possible for a genuine tie) where the predicted side won.
    Returns (accuracy, n_decisive_matches_considered).
    """
    correct = 0
    decisive = 0
    for predicted_home, outcome in zip(predicted_home_win, actual_outcome):
        if outcome == 0.5:
            continue
        decisive += 1
        actual_home_won = outcome == 1.0
        if predicted_home == actual_home_won:
            correct += 1
    return (correct / decisive if decisive else float("nan")), decisive


def margin_mae(predicted_margins: Sequence[float], actual_margins: Sequence[float]) -> float:
    errors = [abs(p - a) for p, a in zip(predicted_margins, actual_margins)]
    return sum(errors) / len(errors) if errors else float("nan")


def brier_score(predicted_home_win_prob: Sequence[float], actual_outcome: Sequence[float]) -> float:
    """Mean squared error between predicted probability and actual outcome
    (1.0 = home win, 0.0 = away win, 0.5 = draw). Lower is better; 0 is
    perfect, 0.25 is what an uninformative constant 50% predictor scores.
    """
    errors = [(p - o) ** 2 for p, o in zip(predicted_home_win_prob, actual_outcome)]
    return sum(errors) / len(errors) if errors else float("nan")


def log_loss(predicted_home_win_prob: Sequence[float], actual_outcome: Sequence[float]) -> float:
    """Cross-entropy between predicted probability and actual outcome.
    Probabilities are clipped away from exactly 0/1 so a single
    overconfident wrong prediction can't produce infinite loss.
    """
    total = 0.0
    for p, o in zip(predicted_home_win_prob, actual_outcome):
        p_clipped = min(max(p, _EPSILON), 1 - _EPSILON)
        total += -(o * math.log(p_clipped) + (1 - o) * math.log(1 - p_clipped))
    return total / len(predicted_home_win_prob) if predicted_home_win_prob else float("nan")


@dataclass(frozen=True)
class CalibrationBucket:
    label: str
    n: int
    mean_predicted_prob: float
    actual_win_rate: float


def calibration_table(
    predicted_home_win_prob: Sequence[float], actual_outcome: Sequence[float], n_bins: int = 10,
) -> List[CalibrationBucket]:
    """Buckets matches by predicted home win probability and compares the
    average predicted probability in each bucket against the actual home
    win rate — a well-calibrated model's two columns should match closely
    (e.g. matches predicted ~70% should actually be won ~70% of the time).
    """
    bucket_width = 1.0 / n_bins
    buckets: Dict[int, List["tuple[float, float]"]] = {i: [] for i in range(n_bins)}
    for p, o in zip(predicted_home_win_prob, actual_outcome):
        index = min(int(p / bucket_width), n_bins - 1)
        buckets[index].append((p, o))

    results = []
    for i in range(n_bins):
        entries = buckets[i]
        low, high = i * bucket_width, (i + 1) * bucket_width
        label = f"{low * 100:.0f}-{high * 100:.0f}%"
        if not entries:
            results.append(CalibrationBucket(label=label, n=0, mean_predicted_prob=float("nan"), actual_win_rate=float("nan")))
            continue
        mean_pred = sum(p for p, _ in entries) / len(entries)
        actual_rate = sum(o for _, o in entries) / len(entries)
        results.append(CalibrationBucket(label=label, n=len(entries), mean_predicted_prob=mean_pred, actual_win_rate=actual_rate))
    return results


@dataclass(frozen=True)
class GroupAccuracy:
    group: str
    n: int
    accuracy: float
    margin_mae: float


def grouped_accuracy(
    rows: Sequence[T], group_key: Callable[[T], str],
    predicted_home_win: Callable[[T], bool], actual_outcome: Callable[[T], float],
    predicted_margin: Callable[[T], float], actual_margin: Callable[[T], float],
    min_n: int = 1,
) -> List[GroupAccuracy]:
    """Generic grouping helper — used for by-season, by-venue, home-vs-away
    (predicted side), and favourite-strength breakdowns alike, so there's
    one implementation of "slice the matches and compute accuracy/MAE per
    slice" rather than one per breakdown.
    """
    groups: Dict[str, List[T]] = {}
    for row in rows:
        groups.setdefault(group_key(row), []).append(row)

    results = []
    for group, group_rows in groups.items():
        if len(group_rows) < min_n:
            continue
        acc, n_decisive = win_accuracy(
            [predicted_home_win(r) for r in group_rows], [actual_outcome(r) for r in group_rows],
        )
        mae = margin_mae(
            [predicted_margin(r) for r in group_rows], [actual_margin(r) for r in group_rows],
        )
        results.append(GroupAccuracy(group=group, n=len(group_rows), accuracy=acc, margin_mae=mae))
    return results
