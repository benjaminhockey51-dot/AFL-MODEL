# AFL Prediction Model — Handover Document

**Baseline:** tag `v1.0.1`, commit `d9271bab7d99d7f9a9b85d990ace741e2620f713`
**Written:** 2026-08-01, at the end of the Stage 1–9 build (`v1.0-pipeline`, commit
`e26b30f11682bc0e115ec091c0fad68879ae31e5`, 168 tests passing). Updated the same day for the
`v1.0.1` patch, which fixed a live production bug found while fact-checking this document —
see §8 for the full writeup. 171 tests passing at this commit.

This document is the source of truth for picking the project back up. It was written to
be read without any prior conversation context — cross-reference the actual code before
trusting anything specific (line numbers, exact values), since the project continues to
evolve after this was written.

---

## 1. Architecture overview

Python 3.9 project, SQLite database (`data/afl.db`, gitignored), SQLAlchemy 2.0 + Alembic
migrations, Typer CLI (`afl-model <command>`), pytest. Lives at
`~/Developer/AFL-Model` — deliberately **not** in iCloud Drive (SQLite + git under iCloud
sync risks silent corruption).

The system is a pipeline, each stage consuming the previous stage's output, all
independently invocable via CLI:

```
ingest (Squiggle + AFL Tables) → ratings engine (walk-forward Elo/attack-defence/form)
  → prediction engine (win%/margin/line/total/confidence) → betting value (edge/EV)
  → reporting (round report, performance tracking) → automation (schedules all of it)
```

A parallel, offline branch validates the pipeline: **backtest** (evaluate a frozen
config against history) and **tuning** (grid-search to find that frozen config).

## 2. Purpose of each package (`src/afl_model/`)

| Package | Purpose |
|---|---|
| `data/` | Source clients (`sources/squiggle.py`, `sources/afl_tables.py`, `sources/afl_tables_parser.py`), ingestion orchestration (`ingest_squiggle.py`, `ingest_afltables.py`, `ingest_odds.py`), cross-source match identity (`match_reconciliation.py`), team/venue name resolution (`team_venue_resolution.py`), venue dedup (`venue_reconciliation.py`) |
| `db/` | SQLAlchemy models (`models/`), Alembic migrations (`migrations/`), connection/session (`connection.py`) |
| `ratings/` | Elo (`elo.py`), attack/defence (`attack_defence.py`), form (`form.py`), rest (`rest.py`), travel/geocoding (`geo_reference.py`), the walk-forward engine (`engine.py`) |
| `models/` | Prediction combination math (`prediction_math.py`) and DB-backed orchestration (`predict.py`) |
| `betting/` | Edge/EV math (`value.py`), odds plugin contract (`odds_client.py`), recommendation orchestration (`recommend.py`) |
| `backtest/` | Walk-forward evaluation of a persisted ratings run: metrics (`metrics.py`), dataset loading from `TeamRatingHistory` (`dataset.py`), ablation/baseline comparison (`evaluate.py`) |
| `tuning/` | Systematic hyperparameter search: grids (`grid.py`), chronological split (`split.py`), the search itself (`search.py`) |
| `reporting/` | Explanation generation (`explain.py`), the full round report (`round_report.py`), real performance tracking (`reconcile.py`, `performance_report.py`) |
| `automation/` | `pipeline.py` — the single scheduled entrypoint chaining everything together |
| `utils/` | Config loading (`config.py`), path resolution (`paths.py`), logging setup (`logging_setup.py`) |
| `cli.py` | Every command below is defined here |

## 3. The pipeline, end to end

1. **Ingestion** — `afl-model ingest-squiggle <year>` (fast, fixtures/results/scores) and
   `afl-model ingest-afltables <year>` (slow, rate-limited to ~1 req/2.5s, attendance +
   full team/player box scores). Both upsert into the same `matches` table via
   `match_reconciliation.upsert_match`, keyed on the **natural key**
   `(season_year, match_date, home_team_id, away_team_id)` — not any one source's ID —
   because Squiggle and AFL Tables number finals rounds differently and disagree in other
   ways. Per-source external IDs live in `match_source_refs`. `afl-model reconcile-venues`
   merges venues that Squiggle names inconsistently across seasons (sponsor renames)
   into AFL Tables' stable slug-keyed canonical venue.
2. **Ratings** — `afl-model run-ratings` walks every completed match chronologically
   (`ratings/engine.py:compute_walk_forward`, a pure/side-effect-free function — no DB
   writes per candidate, which is what makes Stage 7's tuning search tractable). Computes
   Elo (margin-of-victory-damped, season-regressed), attack/defence (points-space,
   dynamically-tracked league average), form (EWMA), rest (signed days vs. baseline),
   travel (haversine km, team home city → venue). Every match gets a **pre-match**
   snapshot per team in `team_rating_history` (append-only, never overwritten — this is
   what makes no-lookahead backtesting possible), and each team's **post-match** state
   after the whole run in `current_team_ratings` (what a live prediction uses).
   `injury_adjustment` is always null — no team-selection data source exists yet.
3. **Prediction** — `afl-model predict <round>` (`models/predict.py` +
   `models/prediction_math.py`). Win probability = Elo + situational adjustments
   (Elo-equivalent points); margin/total = attack/defence only (these two never share
   inputs in this architecture — confirmed by ablation). `predicted_winner` is derived
   from win probability, **not** margin's sign (a real bug, fixed: these are two
   independently-fit signals that can disagree on their own scale, but not on who wins).
   Confidence = distance from a coin flip × ratings maturity (games processed), 0–100.
   Predictions persist to `predictions`, keyed `(match_id, model_version_id)` — idempotent.
4. **Betting** — `afl-model assess-value <round>` compares an existing prediction against
   existing odds (never generates either). No live odds source is connected — see §8.
5. **Reporting** — `afl-model predict <round>` is actually the full experience: the table,
   Highest Confidence Bets, Best Value Bets, Games To Avoid, and a plain-English
   explanation per match (`reporting/explain.py` — cites only factors with nonzero
   prediction-config weight; never fabricates a reason).
6. **Reconciliation** — `afl-model reconcile-predictions` compares every prediction whose
   match has actually been played against the real result → `prediction_results`.
   `afl-model performance-report` summarizes real accuracy from that record. Distinct
   from backtesting (a historical *simulation*): this is what the model *actually said*
   before the fact, compared to reality, accumulating from now forward.
7. **Automation** — `afl-model auto-update` chains ingestion → venue reconcile →
   conditional ratings re-run (only if the count of completed matches for the season
   actually increased — comparing DB state before/after, not trusting ingestion
   summaries' own counters, which are misleadingly truthy on every run) → predict next
   unplayed round → reconcile. Every step is fault-isolated. `deploy/com.aflmodel.autoupdate.plist`
   is a macOS launchd job (daily, 08:00) — **built but not installed/activated**.

## 4. Frozen configuration (`config/config.yaml`)

All values below were set by Stage 7's systematic grid search (`afl-model tune`), not
hand-picked, **except** `form_elo_scale` (see note). Full reasoning and caveats are
inline in `config.yaml` — read them before changing anything.

| Parameter | Frozen value |
|---|---|
| `ratings.elo.k_factor` | 15.0 |
| `ratings.elo.home_ground_advantage` | 35.0 (unchanged from the original prior — search confirmed it) |
| `ratings.elo.season_regression_factor` | 0.65 |
| `ratings.attack_defence.k_factor` | 0.05 |
| `ratings.attack_defence.league_avg_score_ewma_alpha` | 0.01 |
| `prediction.form_elo_scale` | **0.0** — the search's own nominal winner was 10.0, but the margin over 0 was statistically indistinguishable from noise (log loss identical to 4dp). Overridden to 0 for parsimony/robustness, consistent with Stage 5's independent ablation. This was a deliberate human judgment call, made without consulting the holdout. |
| `prediction.rest_elo_scale_per_day` | 0.0 (two independent methods — Stage 5 ablation and Stage 7 search — agree it has no measurable value) |
| `prediction.travel_elo_scale_per_100km` | 2.5 |
| `betting.min_edge_threshold` | `null` — cannot be set without real odds data; never guessed |
| `betting.odds_source` | `null` — no source connected |
| `data.earliest_season` | 2018 (scope boundary, by design) |

**Important assumptions baked into the architecture:**
- Win probability and margin/total are **independent** signals (Elo+situational vs.
  attack/defence) — confirmed by ablation, not assumed. Don't couple them without
  re-validating.
- Match identity is the natural key, not any source's ID — critical for reconciling
  Squiggle/AFL Tables without duplicating or misattributing games.
- AFL Tables' venue **slug** (not display name) is the canonical venue key; Squiggle's
  sponsor-renamed venues are merged into it via a verified (not guessed) mapping in
  `data/venue_reconciliation.py`.
- The 18 current AFL clubs are assumed stable for the whole 2018+ scope — true as of
  writing (GWS, the most recent addition, joined 2012).
- `TeamRatingHistory` is append-only and must never be edited retroactively — it's the
  entire basis for no-lookahead backtesting.

## 5. Validated performance (held-out test, 2025–2026 seasons, n=388, never used for tuning selection)

- **Win accuracy: 71.2%**
- **Brier score: 0.1858**
- **Log loss: 0.5499**
- **Margin MAE: 26.73**

Split: 2018 warm-up only (not scored — cold-start noise), 2019–2024 validation (used for
grid search selection), 2025–2026 held out completely until tuning was finished, touched
exactly once. Elo is the dominant signal — removing it drops accuracy to ~57%, barely
above the 57.1% "always pick home" baseline. Attack/defence make a real, modest
contribution to margin accuracy. Re-run `afl-model backtest --model-version <name>` for
current numbers; re-run `afl-model tune` (a fresh full search, ~5 minutes) if any rating
or prediction-config value changes before trusting new numbers.

## 6. What's complete through Stage 9

| Stage | Status |
|---|---|
| 1. Scaffolding, schema, config, logging, CLI | Done |
| 2. Squiggle ingestion | Done |
| 3. AFL Tables historical backfill (2018–2026) | Done |
| 4. Ratings engine | Done |
| 5. Prediction engine | Done, validated by backtest |
| 6. Betting integration | Pipeline + math done and tested; **no live odds source connected** |
| 7. Backtesting + systematic tuning | Done; ROI-vs-closing-line blocked on odds (see §7) |
| 8. Performance tracking + full reporting | Done |
| 9. Automation | Pipeline done and verified against live data; **LaunchAgent built, not installed/activated** |

Database currently holds: 1,845 matches, 1,368 players, 82,062 player-stat rows, 6
`model_versions`, 9 seasons (2018–2026, 2026 in progress). 41 predictions on file, 0
reconciled — down from 63 and 22 respectively after the v1.0.1 cleanup removed 22
hindsight-contaminated predictions and their reconciled results (see §8).

## 7. Remaining roadmap (priority order)

1. **Activate the LaunchAgent** — deliberately left inactive; a manual deployment step.
2. **Connect a real odds source** (Stage 6) — needs the project owner's own account and
   payment (a paid API — free tiers don't cover AFL); nothing further can happen on
   betting value until this exists.
3. **ROI-vs-closing-line backtesting + an evidence-based betting edge threshold**
   (Stage 7 follow-up) — mechanically blocked on #2.
4. **Stage 10 future extensions** (player disposals, Brownlow modelling, Same Game
   Multi, live predictions, richer finals modelling) — not started, lowest priority.
5. Real performance history accumulates from here forward — not an action item, just
   time that needs to pass.

The round-numbering bug that previously topped this list is fixed as of `v1.0.1` —
see §8's "Resolved" subsection.

## 8. Known technical debt / outstanding issues

**Outstanding:**

1. System Python is 3.9.6 (Apple Command Line Tools build, linked against LibreSSL, past
   its official support window) — an upgrade attempt was blocked earlier by Homebrew's
   installer needing an interactively-typed admin password. Not currently blocking
   anything, but worth revisiting.
2. Betting `min_edge_threshold` and `odds_source` are `null` by design (§7 item 2) — not
   a bug, just incomplete pending a real data source.
3. `injury_adjustment` is always null — no team-selection/injury data source exists.
4. A handful of very-low-sample venues (country-round ovals, one-off China games) have no
   travel coordinates — deliberately left null rather than guessed; harmless (travel
   adjustment just doesn't apply to those specific matches).
5. `model_versions` accumulates one row per `run-ratings`/`auto-update` call that finds
   new data — currently 6 rows, cheap at this scale, but worth a periodic glance if it
   grows into the thousands over years of daily automation.

**Resolved:**

1. **Round-21 hindsight prediction contamination — fixed in `v1.0.1`, commit
   `d9271bab7d99d7f9a9b85d990ace741e2620f713`.**

   - **Root cause:** Squiggle (the upstream fixtures/results API) introduced an "Opening
     Round" (`round: 0`) partway through the 2026 season and retroactively renumbered
     every subsequent round down by one. Our previously-ingested data wasn't re-synced
     before this happened (the LaunchAgent was never installed, so nothing was
     re-ingesting on a schedule — see §7 item 1), so `matches` held stale round numbers
     for rounds 1–20, and — critically — the stale old "round 21" (9 already-played
     matches, 23–26 July) numerically collided with the newly-correct "round 21" (9
     genuinely upcoming matches, 30 July–2 Aug), producing 18 rows under one round
     number instead of ~9. Confirmed directly against Squiggle's live API, game ID by
     game ID, rather than assumed.
   - **Why it produced hindsight predictions:** `auto-update` picks "the next round to
     predict" as the lowest `round_number` with any unplayed match
     (`_find_next_unplayed_round`), then `predict_round` generated predictions for
     *every* match sharing that round number, with no filter on play state. Once the
     round-21 label spanned both play-states, `run_auto_update` predicted all 18
     matches — including the 11 already-played ones by the time this was caught — using
     current (post-hoc) ratings. `reconcile_predictions()` then scored those hindsight
     predictions against the real results indiscriminately, so all 22 rows in
     `prediction_results` (100% of the live performance tracker at the time) were
     contamination. The backtest-validated metrics in §5 were **not** affected — the
     backtest reads only `TeamRatingHistory`, independent of `predictions`/
     `prediction_results`; confirmed by running it against pre- and post-fix database
     snapshots and getting bit-for-bit identical output.
   - **Fix:** `predict_round()` gained a `skip_played` parameter (default `False`,
     preserving its existing manual/CLI spot-check behaviour of intentionally
     re-predicting a past round). `run_auto_update()`, the only fully-unattended caller,
     now passes `skip_played=True`, so automation can never again persist a prediction
     for a match that already has a result — regardless of how a future round-numbering
     anomaly might group matches.
   - **Database remediation:** re-ran `afl-model ingest-squiggle 2026`, which resynced
     `round_number`/`round_name` for 207 matches against Squiggle's corrected numbering
     (0 created — this was purely a label correction) and resolved the round-21
     collision. Deleted the 22 `predictions` rows and 22 `prediction_results` rows
     generated from post-hoc ratings for the 11 matches that were already played at
     generation time; verified afterward that zero remaining `predictions` rows
     reference an already-played match.
   - **Regression tests added:** `test_predict_round_skip_played_excludes_already_played_matches`
     and `test_predict_round_default_still_predicts_played_matches` in `test_predict.py`;
     `test_auto_update_skips_already_played_matches_in_a_mixed_round` in
     `test_automation_pipeline.py`. Full suite: 171 passed (168 baseline + 3 new).

## 9. Environment / setup

```bash
cd ~/Developer/AFL-Model
python3 -m venv .venv          # already exists; recreate only if needed
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head            # schema is already current at this commit
python -m pytest -q             # should show 171 passed
```

- Python 3.9.6 (system/Command Line Tools Python — see debt item 2).
- SQLite DB at `data/afl.db`, **not** committed to git (back up separately —
  `sqlite3 data/afl.db ".backup ..."` or Time Machine).
- `config/config.local.yaml` (gitignored) is where a future odds-API key or personal
  contact email for the Squiggle client would go — never commit secrets to
  `config.yaml`.
- Squiggle: free, no key needed, rate-limited to ~1 req/sec by the client itself.
- AFL Tables: no API, scraped politely (~1 req/2.5s), aggressively cached to
  `data/raw/afltables/` (gitignored) — a full season re-ingest is near-instant from
  cache; only genuinely new pages hit the network.
- Project deliberately lives outside iCloud Drive — do not move it back.

## 10. Baseline reference

**Tag:** `v1.0.1`
**Commit:** `d9271bab7d99d7f9a9b85d990ace741e2620f713`

```bash
git log --oneline v1.0.1 -1
git diff v1.0.1..HEAD            # see everything that's changed since this handover
```

**Previous baseline:** `v1.0-pipeline`, commit `e26b30f11682bc0e115ec091c0fad68879ae31e5`
(end of the Stage 1–9 build) — left unmoved as the historical end-of-build marker.

```bash
git diff v1.0-pipeline..v1.0.1   # exactly the round-21 fix (§8) — 4 files, no data changes
```
