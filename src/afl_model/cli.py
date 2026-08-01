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
    """Predict every match in a round: winner, margin, line, total, win
    probability, confidence — plus highest-confidence picks, best value
    bets (if any odds are connected), games to avoid, and a plain-English
    explanation for every prediction.

    Uses the given ratings run's *current* state, never bookmaker odds for
    the prediction itself — odds are only ever compared afterward.
    """
    import sqlalchemy as sa

    from afl_model.db.connection import get_session
    from afl_model.db.models import Season
    from afl_model.reporting.round_report import build_round_report

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
        report = build_round_report(year, round_number, version_name=model_version)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    def matchup(row) -> str:
        return f"{row.home_team} v {row.away_team}"

    typer.echo(f"\n{year} Round {round_number} predictions:\n")
    header = f"{'Match':38} {'Winner':16} {'Margin':>8} {'Line':>7} {'Total':>7} {'Win %':>7} {'Conf':>6}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in report.matches:
        typer.echo(
            f"{matchup(row):38} {row.predicted_winner:16} {row.predicted_margin:8.1f} "
            f"{row.predicted_line:+7.1f} {row.predicted_total:7.1f} "
            f"{row.home_win_probability * 100:6.1f}% {row.confidence:5.1f}"
        )

    typer.echo("\nHighest Confidence Bets:")
    for row in report.highest_confidence:
        typer.echo(f"  {matchup(row)} -> {row.predicted_winner} ({row.home_win_probability * 100:.0f}% win prob, confidence {row.confidence:.1f})")

    typer.echo("\nBest Value Bets:")
    if not report.best_value:
        typer.echo("  None — no odds source connected yet, or no edge clears the configured threshold.")
    else:
        for row in report.best_value:
            typer.echo(f"  {matchup(row)} -> {row.recommendation} @ {row.bookmaker} (edge {row.edge * 100:+.1f}%)")

    typer.echo("\nGames To Avoid (low confidence / too close to call):")
    if not report.games_to_avoid:
        typer.echo("  None this round.")
    else:
        for row in report.games_to_avoid:
            typer.echo(f"  {matchup(row)} (confidence {row.confidence:.1f})")

    typer.echo("\nWhy:")
    for row in report.matches:
        typer.echo(f"  {matchup(row)}: {row.explanation}")


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


@app.command()
def assess_value(
    round_number: int = typer.Argument(..., help="Round number to assess"),
    year: int = typer.Option(None, "--year", help="Season year (defaults to the most recent season in the database)"),
    model_version: str = typer.Option(None, "--model-version", help="Ratings run to use (defaults to the most recent)"),
    snapshot_type: str = typer.Option("close", "--snapshot", help="Odds snapshot to compare against: open | mid | close"),
) -> None:
    """Compare existing predictions against existing odds for a round and
    recommend Bet Home / Bet Away / No Bet.

    Never recommends a bet without both a real prediction (`afl-model
    predict`) and real odds already on file, and never invents a value
    threshold — with no odds source configured yet (`betting:` in
    config.yaml), every match here reports "No Bet (no odds available)".
    """
    import sqlalchemy as sa

    from afl_model.betting.recommend import assess_round_value
    from afl_model.db.connection import get_session
    from afl_model.db.models import Season

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
        rows = assess_round_value(year, round_number, version_name=model_version, snapshot_type=snapshot_type)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    typer.echo(f"\n{year} Round {round_number} betting value ({snapshot_type} odds):\n")
    header = f"{'Match':38} {'Our winner':16} {'Win %':>7} {'Recommendation':26} {'Book':12} {'Edge':>7} {'EV':>7}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in rows:
        matchup = f"{row.home_team} v {row.away_team}"
        win_pct = f"{row.home_win_probability * 100:.1f}%" if row.home_win_probability is not None else "n/a"
        if row.recommendation == "Bet Home":
            edge, ev = row.home_edge, row.home_ev
        elif row.recommendation == "Bet Away":
            edge, ev = row.away_edge, row.away_ev
        else:
            edge, ev = None, None
        edge_str = f"{edge * 100:+.1f}%" if edge is not None else "-"
        ev_str = f"{ev:+.3f}" if ev is not None else "-"
        typer.echo(
            f"{matchup:38} {row.predicted_winner:16} {win_pct:>7} {row.recommendation:26} "
            f"{row.bookmaker or '-':12} {edge_str:>7} {ev_str:>7}"
        )


@app.command()
def tune() -> None:
    """Systematic hyperparameter search (Stage 7): grid search over rating
    and prediction-combination parameters, scored on validation seasons
    only, then evaluated exactly once on a held-out test period.

    Prints the frozen configuration and both validation (seen during
    search) and test (never touched until this point) metrics. Does NOT
    write to config.yaml — review the result, then apply it deliberately.
    """
    from afl_model.db.connection import get_session
    from afl_model.tuning.search import run_full_search

    session = get_session()
    try:
        result = run_full_search(session)
    finally:
        session.close()

    def fmt_top(candidates, key, n=5):
        return sorted(candidates, key=key)[:n]

    typer.echo("=== Stage A: win-probability search (scored by log loss, validation seasons) ===")
    typer.echo(f"Combinations evaluated: {len(result.win_probability_candidates)}")
    typer.echo("\nTop 5:")
    for c in fmt_top(result.win_probability_candidates, lambda c: c.log_loss):
        typer.echo(
            f"  k={c.k_factor:5.1f} hga={c.home_ground_advantage:5.1f} regress={c.season_regression_factor:.2f} "
            f"form={c.form_elo_scale:5.1f} rest={c.rest_elo_scale_per_day:.1f} travel={c.travel_elo_scale_per_100km:.1f} "
            f"-> logloss={c.log_loss:.4f} brier={c.brier:.4f} acc={c.accuracy:.1%}"
        )

    typer.echo("\n=== Stage B: margin search (scored by MAE, validation seasons) ===")
    typer.echo(f"Combinations evaluated: {len(result.margin_candidates)}")
    typer.echo("\nTop 5:")
    for c in fmt_top(result.margin_candidates, lambda c: c.margin_mae):
        typer.echo(f"  ad_k={c.attack_defence_k_factor:.2f} league_avg_alpha={c.league_avg_score_ewma_alpha:.2f} -> MAE={c.margin_mae:.2f}")

    typer.echo("\n=== Frozen configuration ===")
    e = result.frozen_ratings_config.elo
    ad = result.frozen_ratings_config.attack_defence
    p = result.frozen_prediction_config
    typer.echo(f"elo.k_factor = {e.k_factor}")
    typer.echo(f"elo.home_ground_advantage = {e.home_ground_advantage}")
    typer.echo(f"elo.season_regression_factor = {e.season_regression_factor}")
    typer.echo(f"attack_defence.k_factor = {ad.k_factor}")
    typer.echo(f"attack_defence.league_avg_score_ewma_alpha = {ad.league_avg_score_ewma_alpha}")
    typer.echo(f"prediction.form_elo_scale = {p.form_elo_scale}")
    typer.echo(f"prediction.rest_elo_scale_per_day = {p.rest_elo_scale_per_day}")
    typer.echo(f"prediction.travel_elo_scale_per_100km = {p.travel_elo_scale_per_100km}")

    typer.echo("\n=== Validation seasons (seen during search — reference only) ===")
    v, vm = result.validation_win_probability, result.validation_margin
    typer.echo(f"n={v['n']:.0f} acc={v['accuracy']:.1%} brier={v['brier']:.4f} logloss={v['log_loss']:.4f} margin_MAE={vm['margin_mae']:.2f}")

    typer.echo("\n=== HELD-OUT TEST seasons (never used for selection — the honest report) ===")
    t, tm = result.test_win_probability, result.test_margin
    typer.echo(f"n={t['n']:.0f} acc={t['accuracy']:.1%} brier={t['brier']:.4f} logloss={t['log_loss']:.4f} margin_MAE={tm['margin_mae']:.2f}")


@app.command()
def reconcile_predictions() -> None:
    """Compares every stored prediction whose match has now been played
    against the actual result, recording winner-correct/margin-error/
    total-error/closing-line-diff (predictions_results table). This is
    what lets the software "always know how accurate it has been" — run
    it after each round completes.
    """
    from afl_model.reporting.reconcile import reconcile_predictions as _reconcile

    summary = _reconcile()
    typer.echo(
        f"Checked {summary.predictions_checked} prediction(s): {summary.created} newly reconciled, "
        f"{summary.updated} refreshed, {summary.with_closing_odds} with closing-line odds available."
    )


@app.command()
def performance_report(
    recent_n: int = typer.Option(20, "--recent", help="Number of most recent reconciled predictions for the 'recent form' figure"),
) -> None:
    """Reports how accurate this model has actually been, from every
    reconciled prediction on record (run `reconcile-predictions` first to
    pick up newly completed rounds) — never a backtest or a forecast.
    """
    from afl_model.reporting.performance_report import build_performance_report

    report = build_performance_report(recent_n=recent_n)
    if report.overall_n == 0:
        typer.echo("No reconciled predictions yet — run `afl-model reconcile-predictions` after some rounds have been played.")
        raise typer.Exit(code=0)

    typer.echo(f"\n=== Overall (n={report.overall_n}, {report.overall_n_decisive} decisive) ===")
    acc = f"{report.overall_win_accuracy:.1%}" if report.overall_win_accuracy is not None else "n/a"
    typer.echo(f"Win accuracy: {acc}")
    typer.echo(f"Margin MAE: {report.overall_margin_mae:.2f}" if report.overall_margin_mae is not None else "Margin MAE: n/a")
    typer.echo(f"Total MAE: {report.overall_total_mae:.2f}" if report.overall_total_mae is not None else "Total MAE: n/a")
    if report.n_with_closing_odds:
        typer.echo(f"Mean closing-line diff (n={report.n_with_closing_odds}): {report.mean_closing_line_diff:+.2f}")
    else:
        typer.echo("Closing-line comparison: no odds connected yet.")

    typer.echo(f"\n=== Recent form (last {report.recent_n}) ===")
    recent_acc = f"{report.recent_win_accuracy:.1%}" if report.recent_win_accuracy is not None else "n/a"
    typer.echo(f"Win accuracy: {recent_acc}")
    typer.echo(f"Margin MAE: {report.recent_margin_mae:.2f}" if report.recent_margin_mae is not None else "Margin MAE: n/a")

    typer.echo("\n=== By season ===")
    for s in report.by_season:
        acc = f"{s.win_accuracy:.1%}" if s.win_accuracy is not None else "n/a"
        mae = f"{s.margin_mae:.2f}" if s.margin_mae is not None else "n/a"
        typer.echo(f"{s.season_year}  n={s.n:4}  acc={acc:>6}  margin_MAE={mae}")


if __name__ == "__main__":
    app()
