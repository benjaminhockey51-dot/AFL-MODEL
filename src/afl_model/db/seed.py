from __future__ import annotations

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Team

# The 18 clubs that have competed in the AFL continuously since the 2018
# season (GWS Giants, the most recent addition, joined in 2012) — matches
# the project's 2018-onward scope, so no historical mergers/relocations
# need to be modelled in the Team table.
CURRENT_TEAMS = [
    ("Adelaide Crows", "ADE"),
    ("Brisbane Lions", "BRL"),
    ("Carlton", "CAR"),
    ("Collingwood", "COL"),
    ("Essendon", "ESS"),
    ("Fremantle", "FRE"),
    ("Geelong Cats", "GEE"),
    ("Gold Coast Suns", "GCS"),
    ("GWS Giants", "GWS"),
    ("Hawthorn", "HAW"),
    ("Melbourne", "MEL"),
    ("North Melbourne", "NTH"),
    ("Port Adelaide", "PTA"),
    ("Richmond", "RIC"),
    ("St Kilda", "STK"),
    ("Sydney Swans", "SYD"),
    ("West Coast Eagles", "WCE"),
    ("Western Bulldogs", "WBD"),
]


def seed_teams() -> int:
    """Insert the 18 current AFL clubs if not already present. Idempotent."""
    session = get_session()
    inserted = 0
    try:
        existing_names = set(session.execute(sa.select(Team.name)).scalars().all())
        for name, abbreviation in CURRENT_TEAMS:
            if name in existing_names:
                continue
            session.add(Team(name=name, abbreviation=abbreviation))
            inserted += 1
        session.commit()
    finally:
        session.close()
    return inserted
