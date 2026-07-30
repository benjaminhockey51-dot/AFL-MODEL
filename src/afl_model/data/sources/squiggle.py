from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from afl_model.utils.config import load_config

logger = logging.getLogger(__name__)


class SquiggleClient:
    """Thin wrapper around the Squiggle API (api.squiggle.com.au).

    Squiggle's documented usage policy asks consumers to (a) send a
    descriptive User-Agent, ideally with contact info, and (b) send no more
    than one request per second. Both are enforced here rather than left to
    caller discipline, since this client will run unattended for years.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        config = load_config()["data"]["squiggle"]
        self._base_url = config["base_url"]
        self._min_interval = float(config["min_request_interval_seconds"])
        contact_email = config.get("contact_email")
        contact = f"contact: {contact_email}" if contact_email else "contact info not configured"
        self._user_agent = f"afl-model/0.1 (personal AFL prediction project; {contact})"

        self._session = session or requests.Session()
        self._last_request_time: Optional[float] = None

    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)

        logger.debug("Squiggle request: %s", params)
        response = self._session.get(
            self._base_url,
            params=params,
            headers={"User-Agent": self._user_agent},
            timeout=30,
        )
        self._last_request_time = time.monotonic()
        response.raise_for_status()
        return response.json()

    def get_teams(self) -> List[Dict[str, Any]]:
        """All teams Squiggle has ever tracked, including defunct/renamed ones."""
        return self._get({"q": "teams"})["teams"]

    def get_games(self, year: int, round_number: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fixtures/results for a season, optionally filtered to one round.

        Each game dict includes final scores once played (complete == 100);
        incomplete games have partial/absent score fields.
        """
        params: Dict[str, Any] = {"q": "games", "year": year}
        if round_number is not None:
            params["round"] = round_number
        return self._get(params)["games"]
