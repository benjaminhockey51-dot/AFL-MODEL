from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from afl_model.data.ingest_afltables import ingest_season
from afl_model.db.models import Match, MatchSourceRef, PlayerMatchStats, Team, TeamMatchStats, Venue
from afl_model.db.seed import seed_afltables_team_aliases, seed_teams

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "afltables"


class FakeAFLTablesClient:
    def __init__(self, season_html: str, match_stats_html_by_path: dict):
        self._season_html = season_html
        self._match_stats_html_by_path = match_stats_html_by_path

    def get_season_page(self, year):
        return self._season_html

    def get_match_stats_page(self, relative_path, season_year):
        return self._match_stats_html_by_path[relative_path]


@pytest.fixture()
def seeded(db_session):
    seed_teams()
    seed_afltables_team_aliases()
    return db_session


@pytest.fixture()
def fake_client():
    season_html = (FIXTURES / "2018_round1_single_match_season_fragment.html").read_text(encoding="utf-8")
    match_stats_html = (FIXTURES / "2018_round1_richmond_carlton_stats.html").read_text(encoding="utf-8")
    return FakeAFLTablesClient(
        season_html=season_html,
        match_stats_html_by_path={"../stats/games/2018/031420180322.html": match_stats_html},
    )


def _get_match(session) -> Match:
    richmond_id = session.execute(sa.select(Team.id).where(Team.name == "Richmond")).scalar_one()
    carlton_id = session.execute(sa.select(Team.id).where(Team.name == "Carlton")).scalar_one()
    return session.execute(
        sa.select(Match).where(
            Match.season_year == 2018,
            Match.match_date == date(2018, 3, 22),
            Match.home_team_id == richmond_id,
            Match.away_team_id == carlton_id,
        )
    ).scalar_one()


def test_ingest_season_creates_match_with_attendance_and_venue(seeded, fake_client):
    summary = ingest_season(2018, client=fake_client)

    assert summary.games_seen == 1
    assert summary.matches_created == 1
    assert summary.venues_auto_created == 1
    assert summary.team_stats_written == 2
    assert summary.player_stats_written == 44

    match = _get_match(seeded)
    assert match.attendance == 90151
    assert match.home_points == 121
    assert match.away_points == 95
    assert match.created_by_source == "afltables"

    ref = seeded.execute(
        sa.select(MatchSourceRef).where(MatchSourceRef.match_id == match.id)
    ).scalar_one()
    assert ref.source == "afltables"

    venue = seeded.get(Venue, match.venue_id)
    assert venue.name == "M.C.G."


def test_ingest_season_writes_team_and_player_stats(seeded, fake_client):
    ingest_season(2018, client=fake_client)
    match = _get_match(seeded)

    richmond_id = seeded.execute(sa.select(Team.id).where(Team.name == "Richmond")).scalar_one()
    team_stats = seeded.execute(
        sa.select(TeamMatchStats).where(
            TeamMatchStats.match_id == match.id, TeamMatchStats.team_id == richmond_id
        )
    ).scalar_one()
    assert team_stats.kicks == 207
    assert team_stats.inside_50s == 71

    player_rows = seeded.execute(
        sa.select(PlayerMatchStats).where(PlayerMatchStats.match_id == match.id)
    ).scalars().all()
    assert len(player_rows) == 44  # 22 per team


def test_ingest_season_is_idempotent(seeded, fake_client):
    first = ingest_season(2018, client=fake_client)
    second = ingest_season(2018, client=fake_client)

    assert first.matches_created == 1
    assert second.matches_created == 0
    assert second.matches_resynced == 1

    total_matches = seeded.execute(sa.select(sa.func.count()).select_from(Match)).scalar_one()
    assert total_matches == 1
    total_player_stats = seeded.execute(
        sa.select(sa.func.count()).select_from(PlayerMatchStats)
    ).scalar_one()
    assert total_player_stats == 44  # upserted in place, not duplicated
