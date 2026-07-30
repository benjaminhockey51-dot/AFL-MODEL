from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from afl_model.utils.paths import project_root


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load config/config.yaml, merged with an optional config.local.yaml.

    The local override file (gitignored) is where secrets such as future
    odds-API keys belong — never in config.yaml, which is committed.
    """
    root = project_root()
    base_path = root / "config" / "config.yaml"
    with base_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    local_path = root / "config" / "config.local.yaml"
    if local_path.exists():
        with local_path.open("r", encoding="utf-8") as f:
            local_overrides = yaml.safe_load(f) or {}
        config = _deep_merge(config, local_overrides)

    return config


def database_path() -> Path:
    config = load_config()
    return project_root() / config["database"]["path"]
