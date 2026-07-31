from __future__ import annotations

from typing import FrozenSet

# Chronological split, not random — this is walk-forward time-series data,
# and a random split would be invalid (ratings depend on everything that
# came before them). Seasons, not individual matches, are the unit of
# split: a season is a natural boundary in this domain (finals, list
# changes, rule changes), and splitting mid-season would be arbitrary.
#
# WARMUP_SEASONS (2018): every walk-forward run always processes this —
# ratings need a starting history — but it's excluded from *scoring*
# entirely. Every team starts at the identical default rating in 2018,
# so early-2018 predictions are dominated by cold-start noise that isn't
# really "unfair" to any candidate hyperparameter set, but would add
# noise to whichever config happens to handle it better or worse.
#
# VALIDATION_SEASONS (2019-2024): scored during the hyperparameter search
# below — this is what "best config" is chosen against.
#
# TEST_SEASONS (2025-2026): never scored during search, at all. Touched
# only once, after every tuning decision is frozen, for the final honest
# performance report this stage exists to produce.
WARMUP_SEASONS: FrozenSet[int] = frozenset({2018})
VALIDATION_SEASONS: FrozenSet[int] = frozenset({2019, 2020, 2021, 2022, 2023, 2024})
TEST_SEASONS: FrozenSet[int] = frozenset({2025, 2026})
