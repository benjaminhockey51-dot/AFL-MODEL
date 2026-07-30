from __future__ import annotations

import logging
import logging.config

import yaml

from afl_model.utils.paths import project_root

_configured = False


def configure_logging() -> None:
    """Apply config/logging.yaml. Idempotent — safe to call from every entrypoint."""
    global _configured
    if _configured:
        return

    root = project_root()
    logging_config_path = root / "config" / "logging.yaml"
    with logging_config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    (root / "logs").mkdir(exist_ok=True)

    # RotatingFileHandler filename in logging.yaml is relative to the repo
    # root, not the process's cwd — resolve it explicitly.
    for handler in config.get("handlers", {}).values():
        if "filename" in handler:
            handler["filename"] = str(root / handler["filename"])

    logging.config.dictConfig(config)
    _configured = True
