from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from afl_model.data.venue_reconciliation import reconcile_known_venue_duplicates
from afl_model.db.models import Match, Season, Team, Venue, VenueAlias


def _make_team(session, name: str) -> Team:
    team = Team(name=name, abbreviation=name[:3].upper())
    session.add(team)
    session.flush()
    return team


def test_reconcile_merges_marvel_stadium_into_docklands(db_session):
    session = db_session
    session.add(Season(year=2019))

    docklands = Venue(name="Docklands")
    marvel = Venue(name="Marvel Stadium")
    session.add_all([docklands, marvel])
    session.flush()

    session.add(VenueAlias(venue_id=docklands.id, source="afltables", alias_name="docklands"))
    session.add(VenueAlias(venue_id=marvel.id, source="squiggle", alias_name="Marvel Stadium"))

    bulldogs = _make_team(session, "Western Bulldogs")
    swans = _make_team(session, "Sydney Swans")

    match = Match(
        created_by_source="squiggle",
        created_by_source_match_id="1",
        season_year=2019,
        round_number=1,
        round_name="Round 1",
        is_final=False,
        venue_id=marvel.id,
        match_date=date(2019, 3, 23),
        home_team_id=bulldogs.id,
        away_team_id=swans.id,
    )
    session.add(match)
    session.commit()

    match_id, marvel_id, docklands_id = match.id, marvel.id, docklands.id

    merged = reconcile_known_venue_duplicates()
    assert merged == 1

    session.expire_all()
    refreshed_match = session.get(Match, match_id)
    assert refreshed_match.venue_id == docklands_id

    remaining = session.execute(
        sa.select(Venue).where(Venue.id == marvel_id)
    ).scalar_one_or_none()
    assert remaining is None

    alias = session.execute(
        sa.select(VenueAlias).where(VenueAlias.source == "squiggle", VenueAlias.alias_name == "Marvel Stadium")
    ).scalar_one()
    assert alias.venue_id == docklands_id


def test_reconcile_is_idempotent(db_session):
    merged_first = reconcile_known_venue_duplicates()
    merged_second = reconcile_known_venue_duplicates()
    assert merged_first == 0
    assert merged_second == 0
