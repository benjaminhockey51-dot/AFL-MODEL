from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

import afl_model.automation.pipeline as pipeline_module
from afl_model.data import ingest_afltables, ingest_squiggle
from afl_model.db.models import Match, ModelVersion, Season, Team
from afl_model.ratings.engine import run_ratings_engine


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _make_match(session, season_year, round_number, home, away, home_pts=None, away_pts=None) -> Match:
    match = Match(
        created_by_source="test", created_by_source_match_id=f"{home.id}-{away.id}-{round_number}",
        season_year=season_year, round_number=round_number, round_name=f"Round {round_number}", is_final=False,
        match_date=date(season_year, 3, round_number), home_team_id=home.id, away_team_id=away.id,
        home_points=home_pts, away_points=away_pts,
    )
    session.add(match)
    session.flush()
    return match


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    # Round 1 already played, round 2 not yet.
    _make_match(session, 2026, 1, richmond, adelaide, home_pts=100, away_pts=80)
    _make_match(session, 2026, 2, adelaide, richmond)
    session.commit()
    # Simulates ratings already having been run at least once during
    # initial setup — a fresh install with zero ModelVersions is a
    # separate, real edge case, not what these tests are about.
    run_ratings_engine(version_name="initial-setup")
    return session, richmond, adelaide


def _no_op_squiggle(*args, **kwargs):
    return ingest_squiggle.IngestSummary(year=2026)


def _no_op_afltables(*args, **kwargs):
    return ingest_afltables.IngestSummary(year=2026)


def test_find_next_unplayed_round_returns_lowest_unplayed(setup):
    assert pipeline_module._find_next_unplayed_round(2026) == 2


def test_find_next_unplayed_round_none_when_season_complete(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    _make_match(session, 2026, 1, richmond, adelaide, home_pts=100, away_pts=80)
    session.commit()

    assert pipeline_module._find_next_unplayed_round(2026) is None


def test_auto_update_skips_ratings_rerun_when_no_new_data(setup, monkeypatch):
    monkeypatch.setattr(ingest_squiggle, "ingest_season", _no_op_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    session, *_ = setup
    before = session.execute(sa.select(sa.func.count()).select_from(ModelVersion)).scalar_one()

    summary = pipeline_module.run_auto_update(2026)

    after = session.execute(sa.select(sa.func.count()).select_from(ModelVersion)).scalar_one()
    assert after == before  # no new ModelVersion created
    assert summary.ratings_version_name is None


def _complete_round_2_match(session, season_year=2026):
    """Simulates ingestion finding that a previously-unplayed match has now
    been played — the real trigger condition for a ratings re-run, unlike
    an ingestion summary merely *claiming* a count (which the pipeline
    correctly no longer trusts — see the new_matches comment in
    pipeline.py).
    """
    match = session.execute(
        sa.select(Match).where(Match.season_year == season_year, Match.round_number == 2)
    ).scalar_one()
    match.home_points = 90
    match.away_points = 60
    session.commit()


def test_auto_update_reruns_ratings_when_new_matches_created(setup, monkeypatch):
    session, *_ = setup

    def fake_squiggle(*args, **kwargs):
        _complete_round_2_match(session)
        return ingest_squiggle.IngestSummary(year=2026, matches_resynced=1)

    monkeypatch.setattr(ingest_squiggle, "ingest_season", fake_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    summary = pipeline_module.run_auto_update(2026)

    assert summary.ratings_version_name is not None
    version = session.execute(
        sa.select(ModelVersion).where(ModelVersion.name == summary.ratings_version_name)
    ).scalar_one_or_none()
    assert version is not None


def test_auto_update_twice_same_day_does_not_collide_on_version_name(setup, monkeypatch):
    # Regression test: a version name of just the calendar day collides on
    # any same-day re-run (manual re-trigger, scheduler firing twice,
    # testing) since ModelVersion.name is unique.
    session, *_ = setup
    call_count = {"n": 0}

    def fake_squiggle(*args, **kwargs):
        # Only the first call actually completes a new match — the second
        # run should still not error out even with nothing new to find.
        if call_count["n"] == 0:
            _complete_round_2_match(session)
        call_count["n"] += 1
        return ingest_squiggle.IngestSummary(year=2026, matches_resynced=1)

    monkeypatch.setattr(ingest_squiggle, "ingest_season", fake_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    first = pipeline_module.run_auto_update(2026)
    second = pipeline_module.run_auto_update(2026)

    assert first.errors == []
    assert second.errors == []
    assert first.ratings_version_name is not None
    assert second.ratings_version_name is None  # nothing new the second time


def test_auto_update_predicts_the_next_unplayed_round(setup, monkeypatch):
    monkeypatch.setattr(ingest_squiggle, "ingest_season", _no_op_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    summary = pipeline_module.run_auto_update(2026)

    assert summary.predicted_round == 2
    assert summary.predictions_generated == 1


def test_auto_update_isolates_a_failure_in_one_step(setup, monkeypatch):
    def broken_squiggle(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(ingest_squiggle, "ingest_season", broken_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    summary = pipeline_module.run_auto_update(2026)

    assert summary.squiggle_summary is None
    assert any("squiggle" in e for e in summary.errors)
    # Later steps still ran despite the earlier failure.
    assert summary.predicted_round == 2


def test_auto_update_reconciles_completed_predictions(setup, monkeypatch):
    monkeypatch.setattr(ingest_squiggle, "ingest_season", _no_op_squiggle)
    monkeypatch.setattr(ingest_afltables, "ingest_season", _no_op_afltables)

    summary1 = pipeline_module.run_auto_update(2026)
    assert summary1.predictions_generated == 1  # predicted round 2

    # Now round 2 gets played out.
    session, richmond, adelaide = setup
    match = session.execute(
        sa.select(Match).where(Match.season_year == 2026, Match.round_number == 2)
    ).scalar_one()
    match.home_points = 90
    match.away_points = 60
    session.commit()

    summary2 = pipeline_module.run_auto_update(2026)
    assert summary2.predictions_reconciled >= 1
