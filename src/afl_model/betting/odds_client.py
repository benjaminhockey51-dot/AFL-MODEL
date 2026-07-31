from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ScrapedOdds:
    """One bookmaker's prices for one match, as returned by any odds source.

    Matches are identified by team name + kickoff time here, not by our
    internal match_id — reconciling that to a canonical Match is the
    ingestion layer's job (afl_model.data.ingest_odds), the same pattern
    used for Squiggle/AFL Tables team-name resolution.
    """

    home_team_name: str
    away_team_name: str
    commence_time: datetime
    bookmaker: str
    home_decimal_odds: Optional[float]
    away_decimal_odds: Optional[float]
    home_line: Optional[float]
    away_line: Optional[float]
    total_line: Optional[float]
    snapshot_type: str  # "open" | "mid" | "close"


class OddsClient(Protocol):
    """Contract any odds source must satisfy to plug into ingest_odds.

    No concrete implementation exists yet — the odds source itself is an
    open decision (see the Stage 6 write-up: it needs a paid API and the
    project owner's own account, not something this codebase can decide
    or pay for on its own). Tests exercise this contract with a fake;
    a real implementation (e.g. OddsAPIClient) gets added once a source
    is chosen, following the same real-data-first discipline as every
    other source in this project — team-name aliases for it get seeded
    from that source's *actual* naming convention, verified against live
    data, never guessed.
    """

    def get_odds(self, year: int, round_number: Optional[int] = None) -> List[ScrapedOdds]:
        ...
