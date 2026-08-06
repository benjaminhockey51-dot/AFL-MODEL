from __future__ import annotations

import logging
from datetime import date

import pytest
import sqlalchemy as sa

from afl_model.data.match_reconciliation import MatchUpsertOutcome, NaturalKey, upsert_match
from afl_model.db.models import Match, Season, Team


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    session.commit()
    return session, richmond, adelaide


def _fields(round_number: int, round_name: str, **overrides) -> dict:
    base = dict(round_number=round_number, round_name=round_name, is_final=False)
    base.update(overrides)
    return base


def test_afltables_cannot_overwrite_an_existing_squiggle_round_number(setup):
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    upsert_match(session, "squiggle", "sq-1", key, _fields(21, "Round 21"))
    match, outcome = upsert_match(session, "afltables", "at-1", key, _fields(22, "Round 22"))

    assert outcome is MatchUpsertOutcome.LINKED_EXISTING
    assert match.round_number == 21
    assert match.round_name == "Round 21"


def test_afltables_can_set_round_number_when_match_is_new(setup):
    """AFL Tables must still be able to originate a match (e.g. historical
    backfill running before Squiggle, or Squiggle briefly unavailable) —
    precedence only protects an *existing* Squiggle-set value.
    """
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    match, outcome = upsert_match(session, "afltables", "at-1", key, _fields(21, "Round 21"))

    assert outcome is MatchUpsertOutcome.CREATED
    assert match.round_number == 21
    assert match.round_name == "Round 21"


def test_squiggle_always_overwrites_an_afltables_set_round_number(setup):
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    upsert_match(session, "afltables", "at-1", key, _fields(21, "Round 21"))
    match, outcome = upsert_match(session, "squiggle", "sq-1", key, _fields(20, "Round 20"))

    assert outcome is MatchUpsertOutcome.LINKED_EXISTING
    assert match.round_number == 20
    assert match.round_name == "Round 20"


def test_afltables_resync_does_not_revert_squiggle_round_number(setup):
    """The actual incident this regression guards against: AFL Tables
    re-parses its whole season page every run, so a *resync* (not just a
    first link) from AFL Tables must not silently revert a value Squiggle
    already corrected.
    """
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    upsert_match(session, "afltables", "at-1", key, _fields(21, "Round 21"))
    upsert_match(session, "squiggle", "sq-1", key, _fields(20, "Round 20"))
    match, outcome = upsert_match(session, "afltables", "at-1", key, _fields(21, "Round 21"))

    assert outcome is MatchUpsertOutcome.RESYNCED
    assert match.round_number == 20
    assert match.round_name == "Round 20"


def test_disagreement_logs_a_warning(setup, caplog):
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    upsert_match(session, "squiggle", "sq-1", key, _fields(21, "Round 21"))
    with caplog.at_level(logging.WARNING):
        upsert_match(session, "afltables", "at-1", key, _fields(22, "Round 22"))

    assert any("round_number" in r.message and "afltables" in r.message for r in caplog.records)


def test_non_owned_fields_still_always_apply(setup):
    """Precedence is scoped to round_number/round_name only — every other
    field keeps its existing last-writer-wins behaviour.
    """
    session, richmond, adelaide = setup
    key = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)

    upsert_match(session, "squiggle", "sq-1", key, _fields(21, "Round 21", attendance=30000))
    match, _ = upsert_match(session, "afltables", "at-1", key, _fields(21, "Round 21", attendance=31500))

    assert match.attendance == 31500


def test_order_independence_regression(setup):
    """Direct regression for the live incident: persisted round_number must
    be identical regardless of which order the two sources ingest in.
    """
    session, richmond, adelaide = setup
    key_a = NaturalKey(2026, date(2026, 7, 30), richmond.id, adelaide.id)
    match_a, _ = upsert_match(session, "squiggle", "sq-a", key_a, _fields(21, "Round 21"))
    match_a, _ = upsert_match(session, "afltables", "at-a", key_a, _fields(22, "Round 22"))

    geelong = _make_team(session, "Geelong")
    essendon = _make_team(session, "Essendon")
    key_b = NaturalKey(2026, date(2026, 7, 30), geelong.id, essendon.id)
    match_b, _ = upsert_match(session, "afltables", "at-b", key_b, _fields(22, "Round 22"))
    match_b, _ = upsert_match(session, "squiggle", "sq-b", key_b, _fields(21, "Round 21"))

    assert match_a.round_number == match_b.round_number == 21
