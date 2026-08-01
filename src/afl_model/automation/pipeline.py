from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

import sqlalchemy as sa

from afl_model.data import ingest_afltables, ingest_squiggle
from afl_model.data.venue_reconciliation import reconcile_known_venue_duplicates
from afl_model.db.connection import get_session
from afl_model.db.models import Match
from afl_model.models.predict import predict_round
from afl_model.ratings.engine import run_ratings_engine
from afl_model.reporting.reconcile import reconcile_predictions

logger = logging.getLogger(__name__)


@dataclass
class AutoUpdateSummary:
    season_year: int
    squiggle_summary: Optional[ingest_squiggle.IngestSummary] = None
    afltables_summary: Optional[ingest_afltables.IngestSummary] = None
    venues_merged: int = 0
    ratings_version_name: Optional[str] = None
    predicted_round: Optional[int] = None
    predictions_generated: int = 0
    predictions_reconciled: int = 0
    errors: List[str] = field(default_factory=list)


def _count_completed_matches(season_year: int) -> int:
    session = get_session()
    try:
        return session.execute(
            sa.select(sa.func.count()).select_from(Match).where(
                Match.season_year == season_year, Match.home_points.is_not(None),
            )
        ).scalar_one()
    finally:
        session.close()


def _find_next_unplayed_round(season_year: int) -> Optional[int]:
    """The lowest round number in the season that still has an unplayed
    match — AFL rounds are played in order, so this is "the round
    currently in progress, or next up" without needing any date-based
    guesswork.
    """
    session = get_session()
    try:
        return session.execute(
            sa.select(sa.func.min(Match.round_number)).where(
                Match.season_year == season_year, Match.home_points.is_(None),
            )
        ).scalar_one_or_none()
    finally:
        session.close()


def run_auto_update(season_year: Optional[int] = None) -> AutoUpdateSummary:
    """The single entrypoint a scheduler calls (see afl-model auto-update).

    Every step is independently fault-isolated: a failure partway through
    (a network blip hitting AFL Tables, say) is logged and does not stop
    the remaining steps from running against whatever data already exists
    — predictions for the next round should still be regenerated even if,
    say, the richer AFL Tables stats couldn't be fetched today.

    The ratings engine is only re-run when ingestion actually found new or
    updated matches — re-running it on a no-op day would just create an
    identical ModelVersion + thousands of redundant TeamRatingHistory rows
    for no reason.
    """
    if season_year is None:
        season_year = date.today().year

    summary = AutoUpdateSummary(season_year=season_year)
    completed_before = _count_completed_matches(season_year)

    try:
        summary.squiggle_summary = ingest_squiggle.ingest_season(season_year)
    except Exception as e:
        logger.exception("Squiggle ingestion failed during auto-update")
        summary.errors.append(f"squiggle ingest: {e}")

    try:
        summary.afltables_summary = ingest_afltables.ingest_season(season_year)
    except Exception as e:
        logger.exception("AFL Tables ingestion failed during auto-update")
        summary.errors.append(f"afltables ingest: {e}")

    try:
        summary.venues_merged = reconcile_known_venue_duplicates()
    except Exception as e:
        logger.exception("Venue reconciliation failed during auto-update")
        summary.errors.append(f"venue reconcile: {e}")

    # Ingestion "resyncs" every already-known match on every run regardless
    # of whether anything actually changed, so matches_resynced is truthy
    # almost always — not a usable signal. Comparing the number of
    # *completed* matches before and after is what actually tells us
    # whether new results came in (which is the only thing that changes
    # any rating), so this is measured directly rather than trusted from
    # the ingestion summaries' own counters.
    new_matches = _count_completed_matches(season_year) > completed_before
    if new_matches:
        try:
            # Microsecond precision, not just seconds: ModelVersion.name is
            # unique, and two calls back-to-back (a manual re-trigger, a
            # scheduler firing twice, or simply calling this in a loop)
            # can land within the same wall-clock second.
            version_name = f"auto-{datetime.now():%Y%m%d-%H%M%S%f}"
            run_ratings_engine(version_name=version_name, notes="Automated scheduled update")
            summary.ratings_version_name = version_name
        except Exception as e:
            logger.exception("Ratings engine run failed during auto-update")
            summary.errors.append(f"ratings engine: {e}")
    else:
        logger.info("No new or updated matches found — skipping ratings re-run.")

    try:
        next_round = _find_next_unplayed_round(season_year)
        if next_round is not None:
            rows = predict_round(season_year, next_round, version_name=summary.ratings_version_name)
            summary.predicted_round = next_round
            summary.predictions_generated = len(rows)
        else:
            logger.info("No unplayed matches found for %d — nothing to predict.", season_year)
    except Exception as e:
        logger.exception("Prediction generation failed during auto-update")
        summary.errors.append(f"predict round: {e}")

    try:
        reconcile_summary = reconcile_predictions()
        summary.predictions_reconciled = reconcile_summary.created + reconcile_summary.updated
    except Exception as e:
        logger.exception("Prediction reconciliation failed during auto-update")
        summary.errors.append(f"reconcile predictions: {e}")

    logger.info(
        "Auto-update complete for %d: ratings_run=%s, predicted_round=%s (%d predictions), "
        "%d predictions reconciled, %d error(s)",
        season_year, summary.ratings_version_name, summary.predicted_round,
        summary.predictions_generated, summary.predictions_reconciled, len(summary.errors),
    )
    return summary
