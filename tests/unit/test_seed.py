from __future__ import annotations

import sqlalchemy as sa

from afl_model.db.models import Team
from afl_model.db.seed import CURRENT_TEAMS, seed_teams


def test_seed_teams_inserts_all_18_current_clubs(db_session):
    inserted = seed_teams()
    assert inserted == 18

    count = db_session.execute(sa.select(sa.func.count()).select_from(Team)).scalar_one()
    assert count == len(CURRENT_TEAMS)


def test_seed_teams_is_idempotent(db_session):
    first_run = seed_teams()
    second_run = seed_teams()

    assert first_run == 18
    assert second_run == 0

    count = db_session.execute(sa.select(sa.func.count()).select_from(Team)).scalar_one()
    assert count == 18
