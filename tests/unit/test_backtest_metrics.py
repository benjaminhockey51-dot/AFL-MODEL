from __future__ import annotations

import math

import pytest

from afl_model.backtest.metrics import (
    brier_score,
    calibration_table,
    grouped_accuracy,
    log_loss,
    margin_mae,
    win_accuracy,
)


def test_win_accuracy_basic():
    predicted_home_win = [True, True, False]
    actual_outcome = [1.0, 0.0, 0.0]  # correct, wrong, correct
    accuracy, n = win_accuracy(predicted_home_win, actual_outcome)
    assert accuracy == pytest.approx(2 / 3)
    assert n == 3


def test_win_accuracy_excludes_draws_from_denominator():
    predicted_home_win = [True, True]
    actual_outcome = [1.0, 0.5]  # correct, draw (excluded)
    accuracy, n = win_accuracy(predicted_home_win, actual_outcome)
    assert accuracy == pytest.approx(1.0)
    assert n == 1


def test_win_accuracy_empty_is_nan():
    accuracy, n = win_accuracy([], [])
    assert math.isnan(accuracy)
    assert n == 0


def test_margin_mae_basic():
    predicted = [10.0, -5.0, 0.0]
    actual = [8.0, -10.0, 5.0]
    assert margin_mae(predicted, actual) == pytest.approx(4.0)


def test_brier_score_basic():
    predicted = [0.8, 0.3]
    actual = [1.0, 0.0]
    assert brier_score(predicted, actual) == pytest.approx((0.04 + 0.09) / 2)


def test_brier_score_perfect_predictions_is_zero():
    assert brier_score([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)


def test_log_loss_basic():
    predicted = [0.9]
    actual = [1.0]
    assert log_loss(predicted, actual) == pytest.approx(-math.log(0.9))


def test_log_loss_clips_extreme_probabilities_to_avoid_infinity():
    # A confident-but-wrong prediction (p=1.0 when the answer was 0) would
    # be infinite loss without clipping — must stay finite.
    result = log_loss([1.0], [0.0])
    assert math.isfinite(result)
    assert result > 30  # still heavily penalized, just not infinite


def test_calibration_table_groups_into_correct_buckets():
    predicted = [0.05, 0.15, 0.85, 0.95]
    actual = [0.0, 0.0, 1.0, 1.0]
    buckets = calibration_table(predicted, actual, n_bins=10)
    assert len(buckets) == 10
    populated = [b for b in buckets if b.n > 0]
    assert len(populated) == 4
    assert sum(b.n for b in buckets) == 4


def test_calibration_table_reports_actual_rate_matching_perfect_calibration():
    # Every match predicted ~70% actually won 70% of the time (7 of 10).
    predicted = [0.70] * 10
    actual = [1.0] * 7 + [0.0] * 3
    buckets = calibration_table(predicted, actual, n_bins=10)
    bucket = next(b for b in buckets if b.n == 10)
    assert bucket.mean_predicted_prob == pytest.approx(0.70)
    assert bucket.actual_win_rate == pytest.approx(0.70)


def test_grouped_accuracy_splits_by_key():
    rows = [
        {"season": 2018, "pred_home_win": True, "outcome": 1.0, "pred_margin": 10.0, "actual_margin": 8.0},
        {"season": 2018, "pred_home_win": True, "outcome": 0.0, "pred_margin": 5.0, "actual_margin": -3.0},
        {"season": 2019, "pred_home_win": False, "outcome": 0.0, "pred_margin": -10.0, "actual_margin": -12.0},
    ]
    results = grouped_accuracy(
        rows,
        group_key=lambda r: str(r["season"]),
        predicted_home_win=lambda r: r["pred_home_win"],
        actual_outcome=lambda r: r["outcome"],
        predicted_margin=lambda r: r["pred_margin"],
        actual_margin=lambda r: r["actual_margin"],
    )
    by_group = {r.group: r for r in results}
    assert by_group["2018"].n == 2
    assert by_group["2018"].accuracy == pytest.approx(0.5)
    assert by_group["2019"].n == 1
    assert by_group["2019"].accuracy == pytest.approx(1.0)


def test_grouped_accuracy_respects_min_n():
    rows = [
        {"season": 2018, "pred_home_win": True, "outcome": 1.0, "pred_margin": 1.0, "actual_margin": 1.0},
    ]
    results = grouped_accuracy(
        rows,
        group_key=lambda r: str(r["season"]),
        predicted_home_win=lambda r: r["pred_home_win"],
        actual_outcome=lambda r: r["outcome"],
        predicted_margin=lambda r: r["pred_margin"],
        actual_margin=lambda r: r["actual_margin"],
        min_n=5,
    )
    assert results == []
