from __future__ import annotations

from datetime import date, datetime

import pytest
import sqlalchemy as sa

from afl_model.betting.odds_client import ScrapedOdds
from afl_model.data.ingest_odds import ingest_odds
from afl_model.db.models import Match, Odds, Season, Team, TeamAlias


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def _seed_alias(session, source, alias_name, team):
    session.add(TeamAlias(team_id=team.id, source=source, alias_name=alias_name))


def _make_match(session, home, away, match_date) -> Match:
    match = Match(
        created_by_source="squiggle", created_by_source_match_id=f"{home.id}-{away.id}-{match_date}",
        season_year=match_date.year, round_number=1, round_name="Round 1", is_final=False,
        match_date=match_date, home_team_id=home.id, away_team_id=away.id,
    )
    session.add(match)
    session.flush()
    return match


class FakeOddsClient:
    def __init__(self, quotes):
        self._quotes = quotes

    def get_odds(self, year, round_number=None):
        return self._quotes


@pytest.fixture()
def setup(db_session):
    session = db_session
    session.add(Season(year=2026))
    richmond = _make_team(session, "Richmond")
    adelaide = _make_team(session, "Adelaide Crows")
    _seed_alias(session, "fakeodds", "Richmond", richmond)
    _seed_alias(session, "fakeodds", "Adelaide", adelaide)
    match = _make_match(session, richmond, adelaide, date(2026, 7, 25))
    session.commit()
    return session, richmond, adelaide, match


def test_ingest_odds_attaches_quote_to_existing_match(setup):
    session, richmond, adelaide, match = setup
    quote = ScrapedOdds(
        home_team_name="Richmond", away_team_name="Adelaide",
        commence_time=datetime(2026, 7, 25, 19, 25), bookmaker="TestBook",
        home_decimal_odds=1.85, away_decimal_odds=1.95,
        home_line=-5.5, away_line=5.5, total_line=165.5, snapshot_type="close",
    )
    summary = ingest_odds(2026, FakeOddsClient([quote]), source_name="fakeodds")

    assert summary.quotes_seen == 1
    assert summary.quotes_matched == 1
    assert summary.quotes_unmatched == 0
    assert summary.odds_created == 1

    odds_row = session.execute(sa.select(Odds).where(Odds.match_id == match.id)).scalar_one()
    assert odds_row.bookmaker == "TestBook"
    assert odds_row.home_decimal_odds == pytest.approx(1.85)
    assert odds_row.source == "fakeodds"


def test_ingest_odds_is_idempotent(setup):
    session, richmond, adelaide, match = setup
    quote = ScrapedOdds(
        home_team_name="Richmond", away_team_name="Adelaide",
        commence_time=datetime(2026, 7, 25, 19, 25), bookmaker="TestBook",
        home_decimal_odds=1.85, away_decimal_odds=1.95,
        home_line=-5.5, away_line=5.5, total_line=165.5, snapshot_type="close",
    )
    ingest_odds(2026, FakeOddsClient([quote]), source_name="fakeodds")
    summary = ingest_odds(2026, FakeOddsClient([quote]), source_name="fakeodds")

    assert summary.odds_created == 0
    assert summary.odds_updated == 1
    total = session.execute(sa.select(sa.func.count()).select_from(Odds)).scalar_one()
    assert total == 1


def test_ingest_odds_skips_unmatched_quote_without_crashing(setup):
    session, *_ = setup
    quote = ScrapedOdds(
        home_team_name="Richmond", away_team_name="Adelaide",
        commence_time=datetime(2026, 8, 15, 19, 25), bookmaker="TestBook",  # no match on this date
        home_decimal_odds=1.85, away_decimal_odds=1.95,
        home_line=None, away_line=None, total_line=None, snapshot_type="close",
    )
    summary = ingest_odds(2026, FakeOddsClient([quote]), source_name="fakeodds")

    assert summary.quotes_matched == 0
    assert summary.quotes_unmatched == 1
    total = session.execute(sa.select(sa.func.count()).select_from(Odds)).scalar_one()
    assert total == 0


def test_ingest_odds_raises_for_unresolved_team_alias(setup):
    quote = ScrapedOdds(
        home_team_name="Not A Real Team", away_team_name="Adelaide",
        commence_time=datetime(2026, 7, 25, 19, 25), bookmaker="TestBook",
        home_decimal_odds=1.85, away_decimal_odds=1.95,
        home_line=None, away_line=None, total_line=None, snapshot_type="close",
    )
    with pytest.raises(ValueError, match="No fakeodds team alias"):
        ingest_odds(2026, FakeOddsClient([quote]), source_name="fakeodds")
