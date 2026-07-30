from __future__ import annotations

import sqlalchemy as sa
import pytest
from sqlalchemy.exc import IntegrityError

from afl_model.db.models import Season, Team


EXPECTED_TABLES = {
    "seasons",
    "teams",
    "team_aliases",
    "venues",
    "venue_aliases",
    "players",
    "matches",
    "team_match_stats",
    "player_match_stats",
    "team_selections",
    "model_versions",
    "team_rating_history",
    "predictions",
    "odds",
    "prediction_results",
}


def test_all_expected_tables_exist(db_engine):
    tables = set(sa.inspect(db_engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_team_name_must_be_unique(db_session):
    db_session.add(Team(name="Richmond", abbreviation="RIC"))
    db_session.commit()

    db_session.add(Team(name="Richmond", abbreviation="RIC2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_season_year_is_primary_key(db_session):
    db_session.add(Season(year=2018))
    db_session.commit()

    fetched = db_session.get(Season, 2018)
    assert fetched is not None
    assert fetched.year == 2018
