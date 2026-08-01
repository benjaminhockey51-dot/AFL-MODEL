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

The full output (`afl_model.reporting.round_report`) also groups matches into **Highest
Confidence Bets**, **Best Value Bets** (empty until Stage 6 has a real odds source — never
fabricated), and **Games To Avoid** (confidence below a fixed threshold — genuinely close
calls, not a flaw), plus a plain-English **explanation for every match** built from the
actual numbers behind that specific prediction. The explanation never cites form or rest
as a reason while their prediction-config weights are 0 — doing so would misattribute the
prediction to something that had no effect on it.

Win probability combines Elo with form/rest/travel as Elo-equivalent adjustments; margin
and total come from attack/defence ratings alone (situational adjustments don't extend
to points-space yet — see `prediction:` in `config/config.yaml`). Confidence is built
from two measurable things, never invented: how far the win probability sits from a coin
flip, and how many real matches back the ratings driving it — a lopsided-looking
prediction between two barely-rated teams is *not* actually confident, and this reflects
that.

Every rating and prediction-combination parameter (`elo.k_factor`, `home_ground_advantage`,
`season_regression_factor`, `attack_defence.k_factor`, `league_avg_score_ewma_alpha`,
`form_elo_scale`, `rest_elo_scale_per_day`, `travel_elo_scale_per_100km`) is frozen from a
systematic grid search (Stage 7, below), not hand-picked — see `config/config.yaml` for
the reasoning and honest caveats behind each value.

## Performance tracking

```bash
afl-model reconcile-predictions   # after a round has actually been played
afl-model performance-report
```

Distinct from backtesting (a walk-forward *simulation* over history): this is the
permanent record of what the model actually said, before the fact, compared against what
actually happened — "the software should always know how accurate it has been." A
prediction only ever enters this record once its match has genuinely been played and
`reconcile-predictions` has run; nothing here is ever backfilled from hindsight. Reports
overall and recent-form win accuracy, margin/total MAE, a season-by-season breakdown, and
mean closing-line difference (once real odds exist). Right now this correctly reports
"no reconciled predictions yet" — Round 22, the only round predicted under the current
frozen config, genuinely hasn't been played yet.

## Hyperparameter tuning

```bash
afl-model tune
```

Systematic grid search — not hand-tuning — over every rating/prediction parameter that
plausibly affects accuracy. Chronological split (this is walk-forward time-series data;
a random split would be invalid): 2018 is warm-up only (processed but never scored, since
every team starts at an identical default rating that season — cold-start noise, not a
fair test of any candidate), 2019-2024 is the validation set the search is scored against,
and **2025-2026 is a held-out test period never touched until tuning is completely
finished** — evaluated exactly once, for the honest final report, with no further
adjustment afterward.

Split into two independent stages exploiting real structure in this architecture rather
than brute-forcing blindly: Stage A (`elo.k_factor` × `home_ground_advantage` ×
`season_regression_factor`, outer — each needs its own walk-forward recomputation — ×
`form_elo_scale` × `rest_elo_scale_per_day` × `travel_elo_scale_per_100km`, inner — cheap,
since none of the three ever feed back into a stored rating) is scored by log loss;
Stage B (`attack_defence.k_factor` × `league_avg_score_ewma_alpha`) is scored by margin
MAE independently, since margin/total never depend on Elo or situational signals here
(confirmed by Stage 5's ablation study). 45,360 win-probability combinations and 20 margin
combinations, ~5 minutes total. `afl_model.ratings.engine.compute_walk_forward` is the
pure, side-effect-free function this all runs on — no database writes per candidate,
which is what keeps a search this size tractable.

Held-out test result (2025-2026, n=388, never used for selection): **71.2%** win
accuracy, 0.1858 Brier, 0.5499 log loss, 26.73 margin MAE — a genuine, if modest,
improvement in margin accuracy over the previous hand-adjusted config on the identical
period (27.69 → 26.73 MAE), with win-probability metrics statistically tied either way.

**One deliberate override of the search's own top result**: the grid search's nominal
best `form_elo_scale` was 10.0, not 0 — but its margin over 0 was razor-thin (log loss
identical to 4 decimal places between the two), i.e. statistically indistinguishable from
noise at this sample size, not a real effect. Stage 5's independent ablation study had
already found 0 to be the more defensible value. Rather than retain a possibly-noise
effect just because it happened to rank first, `form_elo_scale` was set to **0** — the
simpler, more parsimonious choice, consistent with two independent methods essentially
agreeing rather than disagreeing. This decision was made by comparing the two candidates'
*validation* performance only, without looking at the 2025-2026 holdout to break the
tie — doing that would have defeated the point of holding it out at all.

## Backtesting

```bash
afl-model backtest --model-version "elo-v4-stage7-tuned"
```

Walk-forward evaluation of a ratings run against every completed match it covers, using
only each match's stored *pre-match* snapshot (`TeamRatingHistory`) — never
`CurrentTeamRating`, which would leak hindsight into the evaluation. Reports win
accuracy, margin MAE, Brier score, log loss, and calibration for the full model, for two
baselines (always-home, Elo-only), and for a leave-one-out ablation of each of the six
Stage 4 rating signals — this is what justified disabling form/rest above, and what
should be re-run after any future rating or prediction-config change before trusting it.

## Betting integration

```bash
afl-model assess-value 22
```

Compares whatever predictions and odds already exist for a round and recommends **Bet
Home** / **Bet Away** / **No Bet** — never generates either on the fly, and never
recommends a bet without a real edge clearing a real, configured threshold.

**No odds source is connected yet.** `afl_model.betting.odds_client.OddsClient` is a
plugin contract (`get_odds(year, round) -> List[ScrapedOdds]`) that any real source
implements — a paid API is the realistic option (AFL coverage needs it; free tiers don't
cover AFL), but that requires an account and payment only the project owner can provide,
so no concrete client exists yet. `afl_model.data.ingest_odds` (the ingestion pipeline
that attaches quotes to existing matches, resolving team names through the same
fail-loud alias mechanism every other source uses) and `afl_model.betting.value` (edge/
EV math, overround removal) are both fully built and tested against a fake client —
wiring in a real source is a small, contained change once one is chosen. Until then,
`betting.min_edge_threshold` in `config/config.yaml` stays `null`, so no bet is ever
recommended — matching `assess-value`'s honest "No Bet (no odds available)" for every
match right now.

## Automation

```bash
afl-model auto-update
```

The single entrypoint a scheduler calls: ingests the latest Squiggle + AFL Tables data
for the current season, reconciles venues, re-runs the ratings engine (only if a match
actually completed since the last run — comparing completed-match counts before and
after ingestion, since ingestion "resyncs" every known match on every run regardless of
whether anything changed, so that alone isn't a usable signal), predicts the next
unplayed round, and reconciles predictions for matches that have since been played.
Every step is fault-isolated — a Squiggle outage is logged and reported but doesn't stop
predictions from being regenerated against whatever data already exists.

Runs independent of any AI assistant session, per the project's original design — this
needs to keep running for years regardless of whether anyone's talking to Claude.
`deploy/com.aflmodel.autoupdate.plist` is a macOS launchd job (daily at 08:00; launchd
runs a missed `StartCalendarInterval` job once the Mac wakes if it was asleep at the
scheduled time) that has **not** been installed or activated — copying a plist into
`~/Library/LaunchAgents/` and loading it starts a persistent background job, so that's a
deliberate decision for you to make, not something done automatically. To install it:

```bash
cp deploy/com.aflmodel.autoupdate.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aflmodel.autoupdate.plist
```

To check it's running: `launchctl list | grep aflmodel`. To stop/remove it:
`launchctl unload ~/Library/LaunchAgents/com.aflmodel.autoupdate.plist && rm ~/Library/LaunchAgents/com.aflmodel.autoupdate.plist`.
Requires at least one manual `afl-model run-ratings` first — a brand new database with
no ratings history can't predict anything.

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
  tuning/         Systematic hyperparameter grid search (train/validation/test split)
  reporting/      "Predict round N" output, performance reports
  automation/     Scheduled end-to-end update pipeline (afl-model auto-update)
  cli.py          Typer CLI entrypoint
deploy/
  com.aflmodel.autoupdate.plist  macOS launchd job (not installed/activated by default)
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
6. Betting integration (edge/EV, value recommendations) — pipeline and math fully built
   and tested; live odds ingestion is pending a real, paid odds source (an account/
   payment decision for the project owner, not something built here yet)
7. Backtesting framework (walk-forward validation, no lookahead) — full walk-forward
   evaluation (`afl_model.backtest`) and systematic hyperparameter tuning
   (`afl_model.tuning`, `afl-model tune`) both built; every rating/prediction parameter is
   now frozen from a grid search validated on a held-out 2025-2026 test period.
   ROI-vs-closing-line and an evidence-based betting edge threshold remain explicitly
   blocked on Stage 6 getting a real odds source — cannot be determined without real
   odds, so this isn't attempted with placeholder data
8. Performance tracking + reporting/CLI — `afl-model predict <round>` is the full
   experience (table, highest-confidence, best-value, games-to-avoid, per-match
   explanations); `afl_model.reporting.reconcile` + `afl-model performance-report` track
   real accuracy over time, distinct from backtesting's historical simulation
9. Automation — `afl-model auto-update` and a macOS launchd job (`deploy/`) are built and
   verified against the real database; the job itself is not installed/activated by
   default (a persistent background job is a deliberate decision, not an automatic one)
   *(current stage)*
10. Future extensions — player disposals, Brownlow modelling, Same Game Multi,
    live predictions, finals modelling
