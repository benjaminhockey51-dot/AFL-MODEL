from __future__ import annotations

import pytest
import sqlalchemy as sa

from afl_model.db.models import Team, Venue, VenueAlias
from afl_model.ratings.geo_reference import (
    haversine_km,
    seed_team_home_locations,
    seed_venue_coordinates,
    travel_distance_km,
)


def test_haversine_zero_distance_for_identical_points():
    assert haversine_km(-37.8136, 144.9631, -37.8136, 144.9631) == pytest.approx(0.0)


def test_haversine_melbourne_to_sydney_is_roughly_right():
    # Real, well-known distance — Melbourne to Sydney is ~710-715km as the
    # crow flies. A wide tolerance since these are city-centre coordinates,
    # not survey points, but this catches a badly wrong formula.
    distance = haversine_km(-37.8136, 144.9631, -33.8688, 151.2093)
    assert 690 <= distance <= 730


def test_travel_distance_km_returns_none_if_either_point_unknown():
    assert travel_distance_km(None, None, -37.8, 144.9) is None
    assert travel_distance_km(-37.8, 144.9, None, None) is None


def test_seed_team_home_locations_is_idempotent_and_does_not_overwrite(db_session):
    session = db_session
    team = Team(name="Richmond", abbreviation="RIC")
    session.add(team)
    session.commit()

    first_pass = seed_team_home_locations()
    assert first_pass == 1
    session.refresh(team)
    assert team.home_city == "Melbourne"

    # Simulate a manual correction — re-running must never clobber it.
    team.home_city = "Manually Corrected City"
    session.commit()

    second_pass = seed_team_home_locations()
    assert second_pass == 0
    session.refresh(team)
    assert team.home_city == "Manually Corrected City"


def test_seed_venue_coordinates_uses_afltables_slug(db_session):
    session = db_session
    venue = Venue(name="M.C.G.")
    session.add(venue)
    session.flush()
    session.add(VenueAlias(venue_id=venue.id, source="afltables", alias_name="mcg"))
    session.commit()

    updated = seed_venue_coordinates()
    assert updated == 1

    session.refresh(venue)
    assert venue.city == "Melbourne"
    assert venue.latitude == pytest.approx(-37.8199)


def test_seed_venue_coordinates_leaves_unknown_venues_null(db_session):
    session = db_session
    venue = Venue(name="Some Obscure Country Oval")
    session.add(venue)
    session.flush()
    session.add(VenueAlias(venue_id=venue.id, source="afltables", alias_name="not_a_known_slug"))
    session.commit()

    updated = seed_venue_coordinates()
    assert updated == 0

    session.refresh(venue)
    assert venue.city is None
    assert venue.latitude is None
