from __future__ import annotations

from afl_model.models.prediction_math import PredictionConfig
from afl_model.utils.config import load_config


def load_prediction_config() -> PredictionConfig:
    raw = load_config()["prediction"]
    return PredictionConfig(
        form_elo_scale=raw["form_elo_scale"],
        rest_elo_scale_per_day=raw["rest_elo_scale_per_day"],
        travel_elo_scale_per_100km=raw["travel_elo_scale_per_100km"],
        line_rounding=raw["line_rounding"],
        confidence_maturity_games=raw["confidence_maturity_games"],
    )
