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
def predict(
    round_number: int = typer.Argument(..., help="Round number to predict"),
    year: int = typer.Option(None, "--year", help="Season year (defaults to the most recent season in the database)"),
    model_version: str = typer.Option(
        None, "--model-version", help="Ratings run to predict from (defaults to the most recent)"
    ),
) -> None:
    """Predict every match in a round: winner, margin, line, total, win probability, confidence.

    Uses the given ratings run's *current* state, never bookmaker odds —
    odds only enter the picture later, for comparison (Stage 6).
    """
    import sqlalchemy as sa

    from afl_model.db.connection import get_session
    from afl_model.db.models import Season
    from afl_model.models.predict import predict_round

    if year is None:
        session = get_session()
        try:
            year = session.execute(sa.select(sa.func.max(Season.year))).scalar_one_or_none()
        finally:
            session.close()
        if year is None:
            typer.echo("No seasons in the database yet.")
            raise typer.Exit(code=1)

    try:
        rows = predict_round(year, round_number, version_name=model_version)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    typer.echo(f"\n{year} Round {round_number} predictions:\n")
    header = f"{'Match':38} {'Winner':16} {'Margin':>8} {'Line':>7} {'Total':>7} {'Win %':>7} {'Conf':>6}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in rows:
        matchup = f"{row.home_team} v {row.away_team}"
        typer.echo(
            f"{matchup:38} {row.predicted_winner:16} {row.predicted_margin:8.1f} "
            f"{row.predicted_line:+7.1f} {row.predicted_total:7.1f} "
            f"{row.home_win_probability * 100:6.1f}% {row.confidence:5.1f}"
        )


@app.command()
def backtest(
    model_version: str = typer.Option(None, "--model-version", help="Ratings run to evaluate (defaults to the most recent)"),
) -> None:
    """Walk-forward backtest: evaluate the prediction engine against every
    completed match using only pre-match rating snapshots, with feature
    ablations and baseline comparisons.
    """
    from afl_model.backtest.evaluate import run_full_backtest
    from afl_model.db.connection import get_session
    from afl_model.models.predict import get_model_version as _get_model_version

    session = get_session()
    try:
        version = _get_model_version(session, model_version)
        report = run_full_backtest(session, version.id)
    finally:
        session.close()

    def fmt_variant(v):
        return (f"{v.name:38} n={v.n:5} decisive={v.n_decisive:5} "
                f"acc={v.win_accuracy:6.1%} MAE={v.margin_mae:6.2f} "
                f"Brier={v.brier:6.4f} LogLoss={v.log_loss:6.4f}")

    typer.echo(f"\nBacktest — model version '{report.model_version_name}'\n")
    typer.echo("=== Full model ===")
    typer.echo(fmt_variant(report.full_model))

    typer.echo("\n=== Baselines ===")
    for b in report.baselines:
        typer.echo(fmt_variant(b))

    typer.echo("\n=== Feature ablations (full model minus one feature) ===")
    for a in report.ablations:
        typer.echo(fmt_variant(a))

    typer.echo("\n=== Calibration (predicted home win % vs actual rate) ===")
    for bucket in report.calibration:
        if bucket.n == 0:
            continue
        typer.echo(f"{bucket.label:10} n={bucket.n:5} predicted={bucket.mean_predicted_prob:6.1%} actual={bucket.actual_win_rate:6.1%}")

    typer.echo("\n=== By season ===")
    for g in sorted(report.by_season, key=lambda g: g.group):
        typer.echo(f"{g.group:10} n={g.n:5} acc={g.accuracy:6.1%} MAE={g.margin_mae:6.2f}")

    typer.echo("\n=== By predicted side ===")
    for g in report.by_predicted_side:
        typer.echo(f"{g.group:24} n={g.n:5} acc={g.accuracy:6.1%} MAE={g.margin_mae:6.2f}")

    typer.echo("\n=== By favourite strength ===")
    for g in sorted(report.by_favourite_strength, key=lambda g: g.group):
        typer.echo(f"{g.group:30} n={g.n:5} acc={g.accuracy:6.1%} MAE={g.margin_mae:6.2f}")

    typer.echo("\n=== By venue (n >= 20) ===")
    for g in sorted(report.by_venue, key=lambda g: -g.n):
        typer.echo(f"{g.group:24} n={g.n:5} acc={g.accuracy:6.1%} MAE={g.margin_mae:6.2f}")


if __name__ == "__main__":
    app()
