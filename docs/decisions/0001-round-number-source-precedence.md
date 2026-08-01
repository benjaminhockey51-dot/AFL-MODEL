# 0001. Deterministic round-number precedence between Squiggle and AFL Tables

**Status:** Proposed — not implemented. Planned as the next piece of work after `v1.0.1`,
not part of it.
**Written:** 2026-08-01, during `v1.0.1` LaunchAgent activation and end-to-end verification.

## Context

While validating the freshly-activated LaunchAgent against live data, a real `auto-update`
run (`ingest_squiggle` → `ingest_afltables`, in that order) silently reverted the
`round_number` of 9 matches that an earlier, targeted `v1.0.1` remediation had corrected —
from 20 back to 21. This is a distinct defect from the `v1.0.1` hindsight-prediction
contamination bug and is deliberately **not** bundled into that release.

### Root cause

`Match.round_number` and `Match.round_name` (`db/models/matches.py`, both `NOT NULL`) are
ordinary mutable columns with no concept of field-level ownership. Both ingesters compute
their own opinion of these fields and pass them through the same generic path:

- `ingest_squiggle.py:79-81` — `round_number=game["round"]`, from Squiggle's live API.
- `ingest_afltables.py:234-235` — `round_number=game.round_number`, parsed by
  `afl_tables_parser.py:137-138` directly from the `"Round N"` text heading on
  afltables.com's own season page.

Both flow into `match_reconciliation.upsert_match()` (`match_reconciliation.py:52-84`),
which — for an existing match, via either the `RESYNCED` or `LINKED_EXISTING` branch —
does an unconditional `for key, value in fields.items(): setattr(match, key, value)`. No
comparison against the current value, no source priority, no logging when two sources
disagree. **Whichever ingester runs last simply overwrites whatever the other one wrote.**

`run_auto_update()` (`automation/pipeline.py`) always calls `ingest_squiggle.ingest_season()`
then `ingest_afltables.ingest_season()`, in that fixed order, every run. That ordering — not
any deliberate precedence decision — is currently the only thing determining which source's
round label survives.

The reason this matters right now: Squiggle changed its 2026 round-numbering scheme
mid-season (added an "Opening Round" / `round: 0`, shifting every subsequent round down by
one — confirmed directly against the live API). **AFL Tables has not made the equivalent
change** — afltables.com's own page still literally displays the heading "Round 21" for the
same nine games Squiggle now calls round 20. This isn't a scraper bug on either side; both
clients faithfully report what their source currently says. The two real-world providers
simply disagree, persistently, for this stretch of the season.

Net effect: for any match where the two sources disagree, `round_number`/`round_name` are
**not idempotent** — they flip-flop deterministically between two known values on every full
`auto-update` run, for as long as the disagreement exists.

**Why this is separate from `v1.0.1`:** verified empirically (three live runs, including
through the newly-activated LaunchAgent) that `skip_played` — which filters purely on
`home_points IS NULL` — generated zero predictions for any match in this churning set,
played or not, regardless of which round label was current at the time. The contamination
fix doesn't depend on round-number correctness at all. This is a real, separate defect:
round-based **reporting and grouping** (`predict_round`'s round selection, `round_report`,
any future round-keyed view) can silently show a different match grouping depending on which
ingester last ran, but predictions themselves are unaffected.

## Options considered

**A — Static per-field source precedence (Squiggle authoritative for `round_number`/
`round_name`), with a fallback for first-write.**
Only Squiggle's ingest may *overwrite* these two fields on an existing match; AFL Tables may
still *set* them if the match doesn't have a value yet (needed because AFL Tables can
legitimately create a match first — e.g. if Squiggle's API is briefly unavailable, or in
edge-case backfill ordering). Squiggle is the natural default authority: it's already the
*sole* source for every unplayed/future match's round number (AFL Tables only ever has
completed games), and it's what `predict_round`'s round-selection logic actually consumes.
*Cost:* small, localized change to `match_reconciliation.py` plus trimming what
`ingest_afltables.py` is allowed to overwrite. No schema migration.

**B — First-writer-wins (freeze the field after initial ingest, from whichever source got
there first).**
Eliminates churn entirely, but freezes whatever value was captured first — which could be
the *pre-correction* value if AFL Tables (or a stale Squiggle cache) wrote first. Doesn't
resolve the disagreement, just picks a winner by accident of ingestion order, and offers no
path for a source's own legitimate future correction to ever land. **Rejected.**

**C — Per-source tracking with explicit resolution (store both sources' opinions, compute
canonical value via an explicit rule).**
Add per-source round columns (or a small side table keyed by `match_id, source`), and derive
the "canonical" `round_number` via a documented resolution function, rather than
denormalizing straight into `Match`. Most auditable and future-proof — trivially extends to
a third source later, or to manual override. But requires an Alembic migration, changes what
every current reader of `Match.round_number` implicitly assumes, and is a materially bigger
surface for a problem whose current impact is cosmetic (reporting/grouping), not
correctness-of-predictions. **Not proportionate as a first fix** — worth revisiting if
disagreements turn out to be frequent or costly.

**D — Reorder the pipeline (afltables before squiggle).**
Cheap, but doesn't fix anything — it just swaps which source's opinion wins by coincidence
of pipeline position, rather than establishing an actual precedence rule. Noted only to rule
out as a false fix.

**E — Conflict detection/logging, as a complement to whichever precedence rule is chosen.**
Log (`WARNING`) whenever a non-authoritative source computes a value that *disagrees* with
what's already stored. Doesn't change behavior, but turns future silent divergences —
including ones not yet discovered, like the finals-round labeling difference already noted
elsewhere (round 25 "Wildcard Finals" appearing before "Finals Week 1" in Squiggle's 2026
data) — into something visible in `logs/afl_model.log` instead of an invisible flip-flop.

## Recommendation

**Option A + Option E.** Deterministic, small, no migration, directly targets the actual
mechanism (unconditional overwrite with no precedence), and adds visibility for the next
disagreement rather than only fixing this one instance.

Concretely:
- In `match_reconciliation.py`, introduce a narrow, explicit constant — e.g.
  `FIELD_SOURCE_PRECEDENCE = {"round_number": "squiggle", "round_name": "squiggle"}` — and
  apply it inside `upsert_match()`'s field-update step: for a key present in that map, only
  apply the incoming value if `source == FIELD_SOURCE_PRECEDENCE[key]`, **or** the match's
  current value for that field is being set for the first time (the `CREATED` path).
- When a non-authoritative source's computed value for an owned field differs from what's
  already stored, log a `WARNING` with both values and the match's natural key.
- No change to `ingest_squiggle.py` — it remains authoritative, unconditionally, as it
  already effectively is for every unplayed match.
- `ingest_afltables.py` keeps passing `round_number`/`round_name` in its `fields` dict (it
  must, for the `CREATED` case) — the precedence enforcement lives centrally in
  `upsert_match()`, not duplicated per-ingester.

## Risks

- **Correctness inversion risk:** if Squiggle is ever transiently wrong while AFL Tables
  happens to be right, this rule prevents AFL Tables from correcting it — the "may still set
  a field with no existing value" fallback doesn't cover *correcting* an existing wrong
  Squiggle value. Mitigation: the conflict-warning log at least surfaces this rather than
  silently entrenching a wrong value; a manual override path is out of scope for this fix.
- **Finals labeling is a different shape of disagreement** than a simple offset. The same
  precedence rule would apply there too (Squiggle wins), but AFL Tables' 2026 finals
  labeling hasn't been specifically checked — needs verifying during rollout, not assumed.
- **Historical seasons (2018–2025) may have pre-existing silent disagreements** that happened
  to resolve "acceptably" by luck of ingestion order over the project's history, and could
  shift once this rule is enforced. Needs a dry-run audit before any bulk re-ingest, not a
  blind fix-and-rerun.
- **Scope creep risk on `upsert_match()`:** it's currently small and generic. The precedence
  logic should stay a narrow, explicit lookup table — not a general-purpose multi-source-merge
  framework this codebase doesn't otherwise need.

## Migration impact

- **No Alembic migration required** for the recommended fix — pure application logic in
  `match_reconciliation.py` plus trimming overwrite behavior in `ingest_afltables.py`'s call
  path. `round_number`/`round_name` stay plain columns.
- **Data remediation on rollout**, in order:
  1. Audit first: a read-only script comparing currently-stored `round_number`/`round_name`
     against both sources' *current* live/scraped values, across **all** seasons
     (2018–2026), not just 2026 round 21 — to find any other pre-existing silent
     disagreements before touching data.
  2. Review the audit output before applying anything — some historical disagreements may be
     intentional/expected and shouldn't be blindly overwritten just because a rule now
     exists.
  3. Run `ingest-squiggle` for affected years to re-assert Squiggle's values under the new
     precedence rule.
  4. Run `ingest-afltables` immediately after and confirm **zero** changes to
     `round_number`/`round_name` this time — this idempotency check (order shouldn't matter
     anymore) is the actual proof the fix works.
- **Optional, deferred:** promoting conflict logging to a persisted, queryable table would
  need its own small migration — not part of the minimal fix; keep the first pass log-only.

## Tests required

No dedicated test file exists yet for `match_reconciliation.py` (currently only exercised
indirectly through the ingest tests). Add a new `tests/unit/test_match_reconciliation.py`:

- AFL Tables ingesting a match **after** Squiggle already set `round_number=X` does **not**
  change it, even when AFL Tables' own data says `Y ≠ X`.
- AFL Tables ingesting a match that **doesn't exist yet** (no prior Squiggle record) **does**
  set `round_number`/`round_name` from its own data — covers the `CREATED` path and
  historical-backfill-before-Squiggle ordering.
- Squiggle ingesting a match **after** AFL Tables already set `round_number=Y` **does**
  overwrite it to Squiggle's `X` — Squiggle always wins going forward, regardless of
  ingestion order.
- A disagreement between an incoming non-authoritative value and the already-stored value
  produces a `WARNING` log entry (via `caplog`) naming both values and the match.
- **The direct regression test for this incident**, in `test_automation_pipeline.py` or a new
  integration-style test: mock both source clients to disagree on round number for the same
  match, run `ingest_squiggle` then `ingest_afltables`, and separately `ingest_afltables`
  then `ingest_squiggle` — assert the persisted `round_number` is **identical regardless of
  call order**. Order-independence is the actual property this fix establishes.
- Full suite must still pass with no unrelated behavior change — existing
  `test_ingest_squiggle.py`/`test_ingest_afltables.py` tests should need no modification if
  the precedence logic is correctly scoped to conflicting-value cases only.

## Status

Not implemented. Deferred as a follow-up to `v1.0.1`, to be picked up as its own scoped
change with its own tests, review, and commit once approved.
