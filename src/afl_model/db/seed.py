from __future__ import annotations

from typing import Dict

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Team, TeamAlias

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


# Squiggle's team names for the 18 current clubs, confirmed directly
# against the live API (GET https://api.squiggle.com.au/?q=teams) rather
# than assumed — Squiggle's naming doesn't always match a club's full
# public name (e.g. "Adelaide" not "Adelaide Crows", "Greater Western
# Sydney" not "GWS Giants").
SQUIGGLE_TEAM_ALIASES: Dict[str, str] = {
    "Adelaide": "Adelaide Crows",
    "Brisbane Lions": "Brisbane Lions",
    "Carlton": "Carlton",
    "Collingwood": "Collingwood",
    "Essendon": "Essendon",
    "Fremantle": "Fremantle",
    "Geelong": "Geelong Cats",
    "Gold Coast": "Gold Coast Suns",
    "Greater Western Sydney": "GWS Giants",
    "Hawthorn": "Hawthorn",
    "Melbourne": "Melbourne",
    "North Melbourne": "North Melbourne",
    "Port Adelaide": "Port Adelaide",
    "Richmond": "Richmond",
    "St Kilda": "St Kilda",
    "Sydney": "Sydney Swans",
    "West Coast": "West Coast Eagles",
    "Western Bulldogs": "Western Bulldogs",
}


def seed_squiggle_team_aliases() -> int:
    """Link each of the 18 current clubs to its Squiggle team-name string.
    Idempotent. Requires seed_teams() to have run first.
    """
    session = get_session()
    inserted = 0
    try:
        existing = set(
            session.execute(
                sa.select(TeamAlias.alias_name).where(TeamAlias.source == "squiggle")
            )
            .scalars()
            .all()
        )
        teams_by_name = {t.name: t for t in session.execute(sa.select(Team)).scalars().all()}

        for squiggle_name, canonical_name in SQUIGGLE_TEAM_ALIASES.items():
            if squiggle_name in existing:
                continue
            team = teams_by_name.get(canonical_name)
            if team is None:
                raise ValueError(
                    f"Cannot seed Squiggle alias '{squiggle_name}': "
                    f"canonical team '{canonical_name}' not found. Run seed_teams() first."
                )
            session.add(TeamAlias(team_id=team.id, source="squiggle", alias_name=squiggle_name))
            inserted += 1
        session.commit()
    finally:
        session.close()
    return inserted


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
