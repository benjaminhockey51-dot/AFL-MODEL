from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Resolve the repository root regardless of current working directory.

    Anchored on this file's location (src/afl_model/utils/paths.py) rather
    than os.getcwd(), so the CLI behaves the same whether invoked from the
    repo root, a cron job, or a test runner.
    """
    return Path(__file__).resolve().parents[3]
