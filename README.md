# AFL Prediction Model

A long-term, professionally-engineered system for producing independent AFL match
predictions (winner, margin, line, total points, win probability, confidence) and
comparing them against bookmaker odds to identify genuine betting value.

The model generates its own prediction **before** consulting bookmaker odds. Odds are
only ever used afterwards, for comparison.

## Status

Under active development. See `docs/architecture.md` (coming as the design solidifies)
and the staged build order below. Each stage is built, reviewed, and approved before
the next begins — nothing here is a finished product yet.

**Scope**: match data from the 2018 AFL season onward.

## Setup

Requires Python 3.9+ (a virtualenv is used regardless of system Python version).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
afl-model seed-teams
afl-model seed-squiggle-aliases
afl-model seed-afltables-aliases
afl-model status
```

## Data ingestion

Two independent sources feed the same canonical `matches` table — see
`afl_model.data.match_reconciliation` for how a match from either source
resolves to one row rather than being duplicated.

```bash
# Fixtures/results (fast — one API call per season)
afl-model ingest-squiggle 2018

# Attendance, venue, and full team/player match stats (slow — rate-limited
# to one request per ~2.5s against afltables.com, and every completed
# season fetches ~200 individual match pages, so a full season takes
# several minutes; already-cached pages skip the wait on a re-run)
afl-model ingest-afltables 2018

# Merge venues Squiggle names inconsistently across seasons (sponsor
# renames) into their AFL Tables canonical equivalent
afl-model reconcile-venues
```

## Ratings engine

```bash
# One-time: populate team home cities and venue coordinates (needed for
# the travel-distance adjustment)
afl-model seed-locations

# Walk every completed match chronologically, computing Elo, attack/defence,
# form, and rest/travel adjustments. Each run creates a new, independent
# ModelVersion + TeamRatingHistory snapshot — re-running after tuning
# config.yaml never overwrites a prior run, so different configurations
# can be compared honestly later (Stage 7).
afl-model run-ratings --version-name "elo-v1"
```

Rating parameters (Elo K-factor, home ground advantage, margin-of-victory damping,
attack/defence smoothing, etc.) live in `config/config.yaml` under `ratings:` — see the
comments there for what each one does and why the defaults are reasonable starting
priors, not asserted-optimal values. `injury_adjustment` is deliberately left null
throughout: it needs team-selection data this project doesn't ingest yet.

## Prediction engine

```bash
afl-model predict 22
```

Predicts every match in a round — winner, margin, line, total, win probability, and a
confidence rating — using a ratings run's *current* state (never bookmaker odds, which
this has no access to at all). Defaults to the most recent season and the most recently
computed ratings run; both are overridable with `--year` and `--model-version`. Every
prediction is persisted (`predictions` table), keyed on `(match_id, model_version_id)`,
so re-running is idempotent rather than duplicating rows.

Win probability combines Elo with form/rest/travel as Elo-equivalent adjustments; margin
and total come from attack/defence ratings alone (situational adjustments don't extend
to points-space yet — see `prediction:` in `config/config.yaml`). Confidence is built
from two measurable things, never invented: how far the win probability sits from a coin
flip, and how many real matches back the ratings driving it — a lopsided-looking
prediction between two barely-rated teams is *not* actually confident, and this reflects
that.

`form_elo_scale` and `rest_elo_scale_per_day` are currently **0** — a 2018-2026
walk-forward backtest (see below) showed both hurting win accuracy, Brier score, and log
loss at their original values, so they're disabled pending proper tuning rather than left
at a guessed weight. Elo and attack/defence are validated, real contributors; travel's
effect was too small and inconsistent in direction to justify changing it either way.

## Backtesting

```bash
afl-model backtest --model-version "elo-v3-for-backtest"
```

Walk-forward evaluation of a ratings run against every completed match it covers, using
only each match's stored *pre-match* snapshot (`TeamRatingHistory`) — never
`CurrentTeamRating`, which would leak hindsight into the evaluation. Reports win
accuracy, margin MAE, Brier score, log loss, and calibration for the full model, for two
baselines (always-home, Elo-only), and for a leave-one-out ablation of each of the six
Stage 4 rating signals — this is what justified disabling form/rest above, and what
should be re-run after any future rating or prediction-config change before trusting it.

## Project layout

```
config/           Tunable YAML config — rating params, thresholds, paths
src/afl_model/
  data/           Source clients, scrapers, ingestion, validation
  db/             SQLAlchemy schema, Alembic migrations, connection layer
  ratings/        Elo, attack/defence, form, travel/rest/injury adjustments
  features/       Feature building for prediction models
  models/         Margin / total / win-probability prediction models
  betting/        Odds ingestion, edge/EV, value recommendations
  backtest/       Walk-forward validation engine, accuracy metrics
  reporting/      "Predict round N" output, performance reports
  cli.py          Typer CLI entrypoint
data/
  raw/            Untouched scraped snapshots (immutable, gitignored)
  staged/         Cleaned intermediate data (gitignored)
  afl.db          SQLite database (gitignored — see Backups below)
tests/            pytest unit + integration tests
docs/             Architecture notes and decision records
logs/             Rotating application logs (gitignored)
```

## Backups

`data/afl.db` is intentionally **not** committed to git (binary, grows large, and every
scraped season is expensive to re-collect). Back it up separately — e.g. Time Machine,
or a periodic `sqlite3 data/afl.db ".backup data/backups/afl_$(date +%Y%m%d).db"` job.
This project deliberately lives outside iCloud Drive to avoid sync-related corruption
of the SQLite database and the git repository itself.

## Staged build order

1. Project scaffolding — repo, config, logging, schema
2. Core ingestion vertical slice (Squiggle API)
3. Historical backfill (AFL Tables, 2018 season onward)
4. Ratings engine (Elo, attack/defence, form, travel/rest/injury adjustments)
5. Prediction engine (winner, margin, line, total, win %, confidence) — validated against
   a full 2018-2026 walk-forward backtest before being finalized
6. Betting integration (odds source, edge/EV, value recommendations) *(current stage)*
7. Backtesting framework (walk-forward validation, no lookahead) — core engine
   (`afl_model.backtest`) already built to validate Stage 5; ROI-vs-closing-line and a
   proper tuning workflow are still to come once Stage 6 provides odds
8. Performance tracking + reporting/CLI (`afl-model predict <round>`)
9. Automation (scheduled auto-update after each completed round)
10. Future extensions — player disposals, Brownlow modelling, Same Game Multi,
    live predictions, finals modelling
