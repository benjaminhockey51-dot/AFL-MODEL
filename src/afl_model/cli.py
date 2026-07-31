from __future__ import annotations

import logging

import typer

from afl_model.utils.logging_setup import configure_logging

app = typer.Typer(help="AFL prediction and betting-value analysis engine.")

configure_logging()
logger = logging.getLogger(__name__)


@app.command()
def status() -> None:
    """Report what's currently in the database (sanity check for Stage 1)."""
    import sqlalchemy as sa

    from afl_model.db.connection import get_engine
    from afl_model.db.models import Match, Season, Team

    engine = get_engine()
    with engine.connect() as conn:
        season_count = conn.execute(sa.select(sa.func.count()).select_from(Season)).scalar_one()
        team_count = conn.execute(sa.select(sa.func.count()).select_from(Team)).scalar_one()
        match_count = conn.execute(sa.select(sa.func.count()).select_from(Match)).scalar_one()

    typer.echo(f"Seasons: {season_count}")
    typer.echo(f"Teams:   {team_count}")
    typer.echo(f"Matches: {match_count}")


@app.command()
def seed_teams() -> None:
    """Insert the 18 current AFL clubs into the database (idempotent)."""
    from afl_model.db.seed import seed_teams as _seed_teams

    inserted = _seed_teams()
    typer.echo(f"Inserted {inserted} team(s) ({18 - inserted} already present).")


@app.command()
def seed_squiggle_aliases() -> None:
    """Link the 18 current clubs to their Squiggle team-name strings (idempotent)."""
    from afl_model.db.seed import seed_squiggle_team_aliases

    inserted = seed_squiggle_team_aliases()
    typer.echo(f"Inserted {inserted} Squiggle team alias(es).")


@app.command()
def ingest_squiggle(
    year: int = typer.Argument(..., help="Season to fetch, e.g. 2018"),
    round_number: int = typer.Option(None, "--round", help="Restrict to a single round"),
) -> None:
    """Fetch fixtures/results from Squiggle and upsert them into the database."""
    from afl_model.data.ingest_squiggle import ingest_season

    summary = ingest_season(year=year, round_number=round_number)
    typer.echo(
        f"{summary.year}: {summary.games_seen} games fetched, "
        f"{summary.matches_created} created, {summary.matches_linked_existing} linked to "
        f"existing matches, {summary.matches_resynced} resynced, "
        f"{summary.venues_auto_created} venue(s) auto-created."
    )


@app.command()
def seed_afltables_aliases() -> None:
    """Link the 18 current clubs to their AFL Tables team-name strings (idempotent)."""
    from afl_model.db.seed import seed_afltables_team_aliases

    inserted = seed_afltables_team_aliases()
    typer.echo(f"Inserted {inserted} AFL Tables team alias(es).")


@app.command()
def ingest_afltables(
    year: int = typer.Argument(..., help="Season to fetch, e.g. 2018"),
) -> None:
    """Fetch results, attendance, and team/player stats from AFL Tables."""
    from afl_model.data.ingest_afltables import ingest_season

    summary = ingest_season(year=year)
    typer.echo(
        f"{summary.year}: {summary.games_seen} games parsed "
        f"({summary.games_incomplete_skipped} incomplete/skipped), "
        f"{summary.matches_created} created, {summary.matches_linked_existing} linked, "
        f"{summary.matches_resynced} resynced, {summary.venues_auto_created} venue(s) auto-created, "
        f"{summary.team_stats_written} team-stat rows, {summary.player_stats_written} player-stat rows, "
        f"{summary.players_created} new player(s)."
    )


@app.command()
def reconcile_venues() -> None:
    """Merge known sponsor-renamed venue duplicates (e.g. Marvel Stadium/Docklands)."""
    from afl_model.data.venue_reconciliation import reconcile_known_venue_duplicates

    merged = reconcile_known_venue_duplicates()
    typer.echo(f"Merged {merged} duplicate venue(s).")


@app.command()
def seed_locations() -> None:
    """Populate team home cities and venue coordinates (idempotent)."""
    from afl_model.ratings.geo_reference import seed_team_home_locations, seed_venue_coordinates

    teams_updated = seed_team_home_locations()
    venues_updated = seed_venue_coordinates()
    typer.echo(f"Updated {teams_updated} team(s), {venues_updated} venue(s) with location data.")


@app.command()
def run_ratings(
    version_name: str = typer.Option(None, "--version-name", help="Name for this rating run (auto-generated if omitted)"),
    notes: str = typer.Option(None, "--notes", help="Optional free-text notes for this run"),
) -> None:
    """Run the Elo/attack-defence/form ratings engine over all completed matches."""
    from afl_model.ratings.engine import run_ratings_engine

    summary = run_ratings_engine(version_name=version_name, notes=notes)
    typer.echo(
        f"'{summary.version_name}': {summary.matches_processed} matches processed, "
        f"{summary.teams_seen} teams, final league avg score {summary.final_league_avg_score:.1f}."
    )


@app.command()
def predict(round_number: int = typer.Argument(..., help="Round number to predict")) -> None:
    """Predict every match in a given round. (Not yet implemented — arrives in Stage 5.)"""
    typer.echo(
        f"Prediction engine isn't built yet (Stage 5). "
        f"Requested: Round {round_number}."
    )
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
