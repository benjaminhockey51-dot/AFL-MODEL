from __future__ import annotations

from datetime import date
from typing import Optional


def rest_days_adjustment(
    last_match_date: Optional[date], this_match_date: date, baseline_days: int
) -> Optional[float]:
    """Signed days of rest relative to a standard week, or None if there's
    no prior match to measure from (a team's first match in the dataset).
    """
    if last_match_date is None:
        return None
    return float((this_match_date - last_match_date).days - baseline_days)
