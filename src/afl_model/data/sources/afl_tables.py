from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from afl_model.utils.config import load_config
from afl_model.utils.paths import project_root

logger = logging.getLogger(__name__)

SEASON_URL_TEMPLATE = "https://afltables.com/afl/seas/{year}.html"


class AFLTablesClient:
    """Fetches pages from afltables.com, an unofficial site with no public
    API — so this client is deliberately more conservative than the
    Squiggle client: a longer minimum gap between requests, and aggressive
    local caching, since a completed match's box score never changes once
    published and re-fetching it later would just be wasted load on
    someone's personal site.

    Cache policy:
      - Match-stats pages are cached forever once fetched (immutable).
      - A season page is cached forever once the season is clearly over
        (i.e. any year before the current one); the current year's page
        is always re-fetched, since it changes weekly during the season,
        but is still written to the cache for debugging/audit purposes.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        config = load_config()["data"]["afltables"]
        self._min_interval = float(config["min_request_interval_seconds"])
        self._user_agent = config["user_agent"]
        self._cache_dir = project_root() / config["raw_cache_dir"]
        self._session = session or requests.Session()
        self._last_request_time: Optional[float] = None

    def _fetch(self, url: str) -> str:
        if self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)

        logger.debug("AFL Tables request: %s", url)
        response = self._session.get(url, headers={"User-Agent": self._user_agent}, timeout=30)
        self._last_request_time = time.monotonic()
        response.raise_for_status()
        return response.text

    def _cached_or_fetch(self, url: str, cache_path: Path, allow_cache: bool) -> str:
        if allow_cache and cache_path.exists():
            logger.debug("Using cached page: %s", cache_path)
            return cache_path.read_text(encoding="utf-8", errors="replace")

        html = self._fetch(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(html, encoding="utf-8")
        return html

    def get_season_page(self, year: int) -> str:
        url = SEASON_URL_TEMPLATE.format(year=year)
        cache_path = self._cache_dir / "seasons" / f"{year}.html"
        season_is_over = year < date.today().year
        return self._cached_or_fetch(url, cache_path, allow_cache=season_is_over)

    def get_match_stats_page(self, relative_path: str, season_year: int) -> str:
        """`relative_path` is the href found on the season page, e.g.
        "../stats/games/2018/031420180322.html" — resolved against the
        season page's own URL, not assumed to be site-root-relative.
        """
        season_url = SEASON_URL_TEMPLATE.format(year=season_year)
        url = urljoin(season_url, relative_path)
        filename = relative_path.rsplit("/", 1)[-1]
        cache_path = self._cache_dir / "match_stats" / str(season_year) / filename
        return self._cached_or_fetch(url, cache_path, allow_cache=True)
