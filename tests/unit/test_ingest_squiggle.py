from __future__ import annotations

import pytest
import sqlalchemy as sa

from afl_model.data.ingest_squiggle import ingest_season
from afl_model.data.team_venue_resolution import resolve_team
from afl_model.db.models import Match, MatchSourceRef, Venue
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


# Real shape observed from the live API on 2026-08-01: a future finals
# slot whose participants aren't decided yet (ladder still in play).
UNSCHEDULED_FINALS_GAME = {
    "id": 38700,
    "year": 2018,
    "round": 26,
    "roundname": "Finals Week 1",
    "date": "2018-09-05 19:20:00",
    "hteam": None,
    "ateam": None,
    "venue": None,
    "complete": 0,
    "is_final": 1,
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


def _match_for_squiggle_id(session, squiggle_id: str) -> Match:
    ref = session.execute(
        sa.select(MatchSourceRef).where(
            MatchSourceRef.source == "squiggle", MatchSourceRef.source_match_id == squiggle_id
        )
    ).scalar_one()
    return session.get(Match, ref.match_id)


def test_resolve_team_raises_for_unknown_alias(seeded):
    with pytest.raises(ValueError, match="No squiggle team alias"):
        resolve_team(seeded, "squiggle", "Not A Real Team")


def test_ingest_season_creates_match_resolves_teams_and_venue(seeded):
    summary = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))

    assert summary.matches_created == 1
    assert summary.matches_resynced == 0
    assert summary.venues_auto_created == 1

    match = _match_for_squiggle_id(seeded, "372")
    assert match.home_points == 121
    assert match.away_points == 95
    assert match.round_name == "Round 1"

    venue = seeded.execute(sa.select(Venue).where(Venue.name == "M.C.G.")).scalar_one()
    assert match.venue_id == venue.id


def test_incomplete_game_scores_left_null(seeded):
    ingest_season(2018, client=FakeSquiggleClient([INCOMPLETE_GAME]))

    match = _match_for_squiggle_id(seeded, "999")
    assert match.home_points is None
    assert match.away_points is None


def test_ingest_season_is_idempotent(seeded):
    first = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))
    assert first.matches_created == 1

    second = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME]))
    assert second.matches_created == 0
    assert second.matches_resynced == 1

    total_matches = seeded.execute(sa.select(sa.func.count()).select_from(Match)).scalar_one()
    assert total_matches == 1


def test_ingest_season_updates_score_once_game_completes(seeded):
    ingest_season(2018, client=FakeSquiggleClient([INCOMPLETE_GAME]))
    completed_version = {**INCOMPLETE_GAME, **{
        "complete": 100, "hgoals": 10, "hbehinds": 5, "hscore": 65,
        "agoals": 9, "abehinds": 4, "ascore": 58,
    }}
    ingest_season(2018, client=FakeSquiggleClient([completed_version]))

    match = _match_for_squiggle_id(seeded, "999")
    assert match.home_points == 65
    assert match.away_points == 58


def test_ingest_season_skips_unscheduled_finals_slots_without_crashing(seeded):
    # Regression test: a future finals slot with no participants decided
    # yet has hteam/ateam == None in the real API — this must be skipped,
    # not crash the whole ingest trying to resolve a "None" team alias.
    summary = ingest_season(2018, client=FakeSquiggleClient([COMPLETE_GAME, UNSCHEDULED_FINALS_GAME]))

    assert summary.games_seen == 2
    assert summary.games_unscheduled_skipped == 1
    assert summary.matches_created == 1  # only the real, complete game
