from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import sqlalchemy as sa

from afl_model.db.connection import get_session
from afl_model.db.models import Team, VenueAlias

# City-level coordinates (not exact stadium geolocation) — sufficient for
# estimating interstate/intercity travel burden, which is what actually
# drives fatigue, not metres. Only venues confirmed with reasonable
# confidence are listed; anything absent is left null rather than guessed
# (a handful of one-off regional/country-round venues fall into this —
# e.g. the exact ground used for some South Australian country games was
# not confidently identified, so it's deliberately omitted here).
#
# Keyed by AFL Tables' stable venue slug (see afl_model.data.venue_reconciliation
# for why that's the reliable identity key, not the raw display string).
VENUE_COORDINATES: Dict[str, Tuple[str, float, float]] = {
    "mcg": ("Melbourne", -37.8199, 144.9834),
    "docklands": ("Melbourne", -37.8163, 144.9475),
    "kardinia_park": ("Geelong", -38.1592, 144.3549),
    "adelaide_oval": ("Adelaide", -34.9156, 138.5961),
    "gabba": ("Brisbane", -27.4858, 153.0381),
    "scg": ("Sydney", -33.8916, 151.2246),
    "showground": ("Sydney", -33.8474, 151.0634),
    "perth": ("Perth", -31.9505, 115.8605),
    "bellerive_oval": ("Hobart", -42.8756, 147.3672),
    "manuka_oval": ("Canberra", -35.3181, 149.1310),
    "marrara_oval": ("Darwin", -12.4003, 130.8827),
    "traeger": ("Alice Springs", -23.6980, 133.8807),
    "carrara": ("Gold Coast", -28.0027, 153.3731),
    "cazalys_stadium": ("Cairns", -16.9430, 145.7040),
    "eureka": ("Ballarat", -37.5622, 143.8503),
    "york_park": ("Launceston", -41.4260, 147.1380),
    "riverway": ("Townsville", -19.3006, 146.7942),
    "stadium_australia": ("Sydney", -33.8470, 151.0634),
    "norwood_oval": ("Adelaide", -34.9163, 138.6310),
    "jiangwan": ("Shanghai", 31.2990, 121.5088),
}

# Each club's home city — used as the travel origin for every match where
# that club is not playing at (or near) that city. City-centre coordinates,
# not a specific training base — the point is interstate travel burden,
# where within-city precision doesn't matter.
TEAM_HOME_LOCATIONS: Dict[str, Tuple[str, float, float]] = {
    "Adelaide Crows": ("Adelaide", -34.9285, 138.6007),
    "Brisbane Lions": ("Brisbane", -27.4698, 153.0251),
    "Carlton": ("Melbourne", -37.8136, 144.9631),
    "Collingwood": ("Melbourne", -37.8136, 144.9631),
    "Essendon": ("Melbourne", -37.8136, 144.9631),
    "Fremantle": ("Perth", -31.9505, 115.8605),
    "Geelong Cats": ("Geelong", -38.1499, 144.3617),
    "Gold Coast Suns": ("Gold Coast", -28.0167, 153.4000),
    "GWS Giants": ("Sydney", -33.8688, 151.2093),
    "Hawthorn": ("Melbourne", -37.8136, 144.9631),
    "Melbourne": ("Melbourne", -37.8136, 144.9631),
    "North Melbourne": ("Melbourne", -37.8136, 144.9631),
    "Port Adelaide": ("Adelaide", -34.9285, 138.6007),
    "Richmond": ("Melbourne", -37.8136, 144.9631),
    "St Kilda": ("Melbourne", -37.8136, 144.9631),
    "Sydney Swans": ("Sydney", -33.8688, 151.2093),
    "West Coast Eagles": ("Perth", -31.9505, 115.8605),
    "Western Bulldogs": ("Melbourne", -37.8136, 144.9631),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def seed_team_home_locations() -> int:
    """Populate Team.home_city/home_latitude/home_longitude for teams that
    don't already have it set. Idempotent — never overwrites an existing
    (possibly manually-corrected) value.
    """
    session = get_session()
    updated = 0
    try:
        teams = session.execute(sa.select(Team)).scalars().all()
        for team in teams:
            if team.home_city is not None:
                continue
            location = TEAM_HOME_LOCATIONS.get(team.name)
            if location is None:
                continue
            city, lat, lon = location
            team.home_city, team.home_latitude, team.home_longitude = city, lat, lon
            updated += 1
        session.commit()
    finally:
        session.close()
    return updated


def seed_venue_coordinates() -> int:
    """Populate Venue.city/latitude/longitude for every venue whose AFL
    Tables slug is in VENUE_COORDINATES and doesn't already have it set.
    Idempotent.
    """
    session = get_session()
    updated = 0
    try:
        aliases = session.execute(
            sa.select(VenueAlias).where(VenueAlias.source == "afltables")
        ).scalars().all()
        for alias in aliases:
            location = VENUE_COORDINATES.get(alias.alias_name)
            if location is None:
                continue
            venue = alias.venue
            if venue.city is not None:
                continue
            city, lat, lon = location
            venue.city, venue.latitude, venue.longitude = city, lat, lon
            updated += 1
        session.commit()
    finally:
        session.close()
    return updated


def travel_distance_km(
    home_lat: Optional[float], home_lon: Optional[float],
    venue_lat: Optional[float], venue_lon: Optional[float],
) -> Optional[float]:
    """Distance from a team's home city to a match venue, or None if either
    location is unknown — never guessed.
    """
    if home_lat is None or home_lon is None or venue_lat is None or venue_lon is None:
        return None
    return haversine_km(home_lat, home_lon, venue_lat, venue_lon)
