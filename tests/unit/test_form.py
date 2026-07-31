from __future__ import annotations

import pytest

from afl_model.ratings.form import update_form


def test_win_when_expected_to_lose_pushes_form_up():
    new_form = update_form(previous_form=0.0, actual=1.0, expected=0.2, alpha=0.25)
    assert new_form > 0.0


def test_loss_when_expected_to_win_pushes_form_down():
    new_form = update_form(previous_form=0.0, actual=0.0, expected=0.8, alpha=0.25)
    assert new_form < 0.0


def test_meeting_expectation_leaves_form_near_previous():
    new_form = update_form(previous_form=0.1, actual=0.6, expected=0.6, alpha=0.25)
    assert new_form == pytest.approx(0.1 * 0.75)


def test_higher_alpha_reacts_faster_to_new_result():
    slow = update_form(previous_form=0.0, actual=1.0, expected=0.0, alpha=0.1)
    fast = update_form(previous_form=0.0, actual=1.0, expected=0.0, alpha=0.5)
    assert fast > slow
