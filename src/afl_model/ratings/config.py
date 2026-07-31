from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from afl_model.ratings.attack_defence import AttackDefenceConfig
from afl_model.ratings.elo import EloConfig
from afl_model.utils.config import load_config


@dataclass(frozen=True)
class RatingsConfig:
    elo: EloConfig
    attack_defence: AttackDefenceConfig
    form_ewma_alpha: float
    rest_baseline_days: int
    travel_enabled: bool

    def to_json(self) -> str:
        return json.dumps({
            "elo": asdict(self.elo),
            "attack_defence": asdict(self.attack_defence),
            "form_ewma_alpha": self.form_ewma_alpha,
            "rest_baseline_days": self.rest_baseline_days,
            "travel_enabled": self.travel_enabled,
        }, indent=2, sort_keys=True)


def load_ratings_config() -> RatingsConfig:
    raw = load_config()["ratings"]
    return RatingsConfig(
        elo=EloConfig(**raw["elo"]),
        attack_defence=AttackDefenceConfig(**raw["attack_defence"]),
        form_ewma_alpha=raw["form"]["ewma_alpha"],
        rest_baseline_days=raw["adjustments"]["rest"]["baseline_days"],
        travel_enabled=raw["adjustments"]["travel"]["enabled"],
    )
