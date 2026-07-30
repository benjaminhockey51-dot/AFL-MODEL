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
        f"{summary.matches_created} created, {summary.matches_updated} updated, "
        f"{summary.venues_auto_created} venue(s) auto-created."
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
