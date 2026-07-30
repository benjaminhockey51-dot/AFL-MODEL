from __future__ import annotations

import pytest
import sqlalchemy as sa

from afl_model.data.ingest_squiggle import ingest_season, resolve_team
from afl_model.db.connection import get_session
from afl_model.db.models import Match, Venue
from afl_model.db.seed import seed_squiggle_team_aliases, seed_teams

COMPLETE_GAME = {
    "id": 372,
    "year": 2018,
    "round": 1,
    "roundname": "Round 1",
    "date": "2018-03-22 19:25:00",
    "hteam": "Richmond",
    "ateam": "Carlton",
    "venue": "M.C.G.",
    "complete": 100,
    "is_final": 0,
    "hgoals": 17,
    "hbehinds": 19,
    "hscore": 121,
    "agoals": 15,
    "abehinds": 5,
    "ascore": 95,
}

INCOMPLETE_GAME = {
    "id": 999,
    "year": 2018,
    "round": 2,
    "roundname": "Round 2",
    "date": "2018-03-29 19:25:00",
    "hteam": "Richmond",
    "ateam": "Carlton",
    "venue": "M.C.G.",
    "complete": 0,
    "is_final": 0,
    "hgoals": None,
    "hbehinds": None,
    "hscore": None,
    "agoals": None,
    "abehinds": None,
    "ascore": None,
}


class FakeSquiggleClient:
    def __init__(self, games):
        self._games = games

    def get_games(self, year, round_number=None):
        return [g for g in self._games if round_number is None or g["round"] == round_number]


@pytest.fixture()
def seeded(db_session):
    seed_teams()
    seed_squiggle_team_aliases()
    return db_session


def test_resolve_team_raises_for_unknown_alias(seeded):
    with pytest.raises(ValueError, match="No Squiggle team alias"):
        resolve_team(seeded, "Not A Real Team")


def test_ingest_season_creates_match_resolves_teams_and_venue(seeded):
    summary = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))

    assert summary.matches_created == 1
    assert summary.matches_updated == 0
    assert summary.venues_auto_created == 1

    match = seeded.execute(
        sa.select(Match).where(Match.source == "squiggle", Match.source_match_id == "372")
    ).scalar_one()
    assert match.home_points == 121
    assert match.away_points == 95
    assert match.round_name == "Round 1"

    venue = seeded.execute(sa.select(Venue).where(Venue.name == "M.C.G.")).scalar_one()
    assert match.venue_id == venue.id


def test_incomplete_game_scores_left_null(seeded):
    ingest_season(2018, client=FakeSquiggleClient([INCOMPLETE_GAME]))

    match = seeded.execute(
        sa.select(Match).where(Match.source == "squiggle", Match.source_match_id == "999")
    ).scalar_one()
    assert match.home_points is None
    assert match.away_points is None


def test_ingest_season_is_idempotent(seeded):
    first = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))
    assert first.matches_created == 1

    second = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))
    assert second.matches_created == 0
    assert second.matches_updated == 1

    total_matches = seeded.execute(sa.select(sa.func.count()).select_from(Match)).scalar_one()
    assert total_matches == 1


def test_ingest_season_updates_score_once_game_completes(seeded):
    ingest_season(2018, client=FakeSquiggleClient([INCOMPLETE_GAME]))
    completed_version = {**INCOMPLETE_GAME, **{
        "complete": 100, "hgoals": 10, "hbehinds": 5, "hscore": 65,
        "agoals": 9, "abehinds": 4, "ascore": 58,
    }}
    ingest_season(2018, client=FakeSquiggleClient([completed_version]))

    match = seeded.execute(
        sa.select(Match).where(Match.source == "squiggle", Match.source_match_id == "999")
    ).scalar_one()
    assert match.home_points == 65
    assert match.away_points == 58
