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
3. Historical backfill (AFL Tables, 2018 season onward) *(current stage)*
4. Ratings engine (Elo, attack/defence, form, travel/rest/injury adjustments)
5. Prediction engine (winner, margin, line, total, win %, confidence)
6. Betting integration (odds source, edge/EV, value recommendations)
7. Backtesting framework (walk-forward validation, no lookahead)
8. Performance tracking + reporting/CLI (`afl-model predict <round>`)
9. Automation (scheduled auto-update after each completed round)
10. Future extensions — player disposals, Brownlow modelling, Same Game Multi,
    live predictions, finals modelling
