from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from afl_model.utils.config import load_config


@dataclass(frozen=True)
class BettingConfig:
    odds_source: Optional[str]
    min_edge_threshold: Optional[float]


def load_betting_config() -> BettingConfig:
    raw = load_config()["betting"]
    return BettingConfig(
        odds_source=raw.get("odds_source"),
        min_edge_threshold=raw.get("min_edge_threshold"),
    )
