from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# AFL Tables labels each week of finals individually rather than with a
# round number the way Squiggle does. This is the standard AFL final-8
# structure used in every season from 2018 onward (this project's scope),
# confirmed against real 2018 season data: Qualifying/Elimination Finals
# both fall in the first final's week, then one round each for Semi,
# Preliminary, and the Grand Final.
FINALS_ROUND_NUMBERS = {
    "Qualifying Final": 24,
    "Elimination Final": 24,
    "Semi Final": 25,
    "Preliminary Final": 26,
    "Grand Final": 27,
}

DATE_VENUE_RE = re.compile(
    r"(?P<date>\w+ \d{2}-\w{3}-\d{4})\s+(?P<time>\d{1,2}:\d{2} [AP]M)"
    # "Att: N" is entirely absent, not just zero, for the 2020 COVID season's
    # crowd-less games — real data, discovered when the original regex (which
    # required the literal "Att:" text) silently dropped every 2020 match
    # with no listed attendance instead of just leaving attendance null.
    r".*?(?:Att:\s*(?P<attendance>[\d,]+))?\s*Venue:\s*(?P<venue>.*)$",
    re.DOTALL,
)
SCORE_TOKEN_RE = re.compile(r"(\d+)\.(\d+)")


@dataclass
class ScrapedGame:
    round_name: str
    round_number: int
    is_final: bool
    home_team: str
    away_team: str
    home_goals: Optional[int]
    home_behinds: Optional[int]
    home_points: Optional[int]
    away_goals: Optional[int]
    away_behinds: Optional[int]
    away_points: Optional[int]
    match_date_str: Optional[str]  # e.g. "Thu 22-Mar-2018 7:25 PM" (venue-local)
    attendance: Optional[int]
    venue_name: Optional[str]
    venue_slug: Optional[str]
    match_stats_path: Optional[str]  # relative path, e.g. "../stats/games/2018/....html"


def _parse_score_from_progression(tt_text: str) -> "tuple[Optional[int], Optional[int]]":
    tokens = SCORE_TOKEN_RE.findall(tt_text)
    if not tokens:
        return None, None
    goals, behinds = tokens[-1]
    return int(goals), int(behinds)


def _parse_date_venue_cell(cell) -> Dict[str, Optional[str]]:
    text = cell.get_text(" ", strip=True)
    result: Dict[str, Optional[str]] = {
        "match_date_str": None, "attendance": None, "venue_name": None, "venue_slug": None,
    }
    match = DATE_VENUE_RE.search(text)
    if match:
        result["match_date_str"] = f"{match.group('date')} {match.group('time')}"
        if match.group("attendance"):
            result["attendance"] = int(match.group("attendance").replace(",", ""))
        result["venue_name"] = match.group("venue").strip()

    venue_link = cell.find("a", href=re.compile(r"venues/"))
    if venue_link is not None:
        slug_match = re.search(r"venues/([a-z0-9_]+)\.html", venue_link["href"])
        if slug_match:
            result["venue_slug"] = slug_match.group(1)
        if not result["venue_name"]:
            result["venue_name"] = venue_link.get_text(strip=True)

    return result


def parse_season_page(html: str, year: int) -> List[ScrapedGame]:
    """Parse an AFL Tables season page (afltables.com/afl/seas/{year}.html).

    Round-header tables (border=2) and individual match tables (border=1,
    two rows: home team then away team) are visually distinguishable in
    the page's own markup — confirmed by inspecting real pages, not assumed.
    """
    soup = BeautifulSoup(html, "lxml")
    games: List[ScrapedGame] = []
    current_round_name = None

    for table in soup.find_all("table"):
        border = table.get("border")
        rows = table.find_all("tr", recursive=False)

        if border == "2" and rows:
            header_text = rows[0].get_text(" ", strip=True)
            # e.g. "Round 1" possibly followed by attendance summary text,
            # or a standalone "Qualifying Final" / "Finals" section marker.
            round_match = re.match(r"(Round \d+|Qualifying Final|Elimination Final|"
                                    r"Semi Final|Preliminary Final|Grand Final)", header_text)
            if round_match:
                current_round_name = round_match.group(1)
            continue

        if border != "1" or len(rows) != 2 or current_round_name is None:
            continue

        home_cells = rows[0].find_all("td", recursive=False)
        away_cells = rows[1].find_all("td", recursive=False)
        if len(home_cells) < 4 or len(away_cells) < 4:
            continue  # not a match table in the expected shape — skip, don't guess

        home_link = home_cells[0].find("a")
        away_link = away_cells[0].find("a")
        if home_link is None or away_link is None:
            continue

        home_goals, home_behinds = _parse_score_from_progression(home_cells[1].get_text())
        away_goals, away_behinds = _parse_score_from_progression(away_cells[1].get_text())
        home_points_text = home_cells[2].get_text(strip=True)
        away_points_text = away_cells[2].get_text(strip=True)

        date_venue = _parse_date_venue_cell(home_cells[3])

        match_stats_path = None
        stats_link = away_cells[3].find("a", href=re.compile(r"stats/games/"))
        if stats_link is not None:
            match_stats_path = stats_link["href"]

        if current_round_name.startswith("Round "):
            round_number = int(current_round_name.split()[1])
            is_final = False
        else:
            round_number = FINALS_ROUND_NUMBERS[current_round_name]
            is_final = True

        games.append(ScrapedGame(
            round_name=current_round_name,
            round_number=round_number,
            is_final=is_final,
            home_team=home_link.get_text(strip=True),
            away_team=away_link.get_text(strip=True),
            home_goals=home_goals,
            home_behinds=home_behinds,
            home_points=int(home_points_text) if home_points_text.isdigit() else None,
            away_goals=away_goals,
            away_behinds=away_behinds,
            away_points=int(away_points_text) if away_points_text.isdigit() else None,
            match_date_str=date_venue["match_date_str"],
            attendance=date_venue["attendance"],
            venue_name=date_venue["venue_name"],
            venue_slug=date_venue["venue_slug"],
            match_stats_path=match_stats_path,
        ))

    return games


def resolve_match_stats_url(base_season_url: str, match_stats_path: str) -> str:
    return urljoin(base_season_url, match_stats_path)


@dataclass
class ScrapedPlayerStat:
    team_name: str
    player_name: str  # "Last, First" as displayed
    player_url: Optional[str]  # relative href, stable per-player identifier
    stats: Dict[str, Optional[int]] = field(default_factory=dict)
    time_on_ground_pct: Optional[float] = None


@dataclass
class ScrapedTeamMatchStats:
    team_name: str
    stats: Dict[str, Optional[int]] = field(default_factory=dict)


# Maps AFL Tables' stat-table header abbreviations to our schema's field names.
STAT_COLUMN_MAP = {
    "KI": "kicks", "MK": "marks", "HB": "handballs", "DI": "disposals",
    "GL": "goals", "BH": "behinds", "HO": "hitouts", "TK": "tackles",
    "RB": "rebound_50s", "IF": "inside_50s", "CL": "clearances", "CG": "clangers",
    "FF": "frees_for", "FA": "frees_against", "BR": "brownlow_votes",
    "CP": "contested_possessions", "UP": "uncontested_possessions",
    "CM": "contested_marks", "MI": "marks_inside_50", "1%": "one_percenters",
    "BO": "bounces", "GA": "goal_assists",
}


def _parse_int_cell(text: str) -> Optional[int]:
    text = text.strip()
    return int(text) if text.isdigit() else None


def parse_match_stats_page(
    html: str,
) -> "tuple[List[ScrapedTeamMatchStats], List[ScrapedPlayerStat]]":
    """Parse an AFL Tables match-stats page into team-total and per-player rows.

    Each team's "Match Statistics" table (border implied by table order, not
    checked here — identified instead by its own heading, e.g. "Richmond
    Match Statistics") has a header row, one row per player, then a
    "Totals" row (the team-level aggregate) and an "Opposition" row (a
    redundant cross-check — the opponent's totals, ignored here since the
    opponent's own table is the primary source for its stats).
    """
    soup = BeautifulSoup(html, "lxml")
    team_stats: List[ScrapedTeamMatchStats] = []
    player_stats: List[ScrapedPlayerStat] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        heading = rows[0].get_text(" ", strip=True)
        heading_match = re.match(r"(.+?) Match Statistics", heading)
        if not heading_match or len(rows) < 3:
            continue

        team_name = heading_match.group(1)
        # The header row ("#", "Player", "KI", ...) uses <th>; every data
        # row uses <td> — confirmed by inspecting the real page, not assumed.
        header_cells = [c.get_text(strip=True) for c in rows[1].find_all("th")]

        for row in rows[2:]:
            cells = row.find_all("td")
            if len(cells) == len(header_cells):
                # A per-player row: "#", "Player", stat columns...
                label = cells[1].get_text(strip=True)
                values = {header_cells[i]: cells[i].get_text(strip=True) for i in range(len(cells))}
            elif len(cells) == len(header_cells) - 1:
                # "Totals"/"Opposition" rows have no jumper-number column,
                # so the label sits where "Player" would and everything
                # after it shifts one column left relative to the header.
                label = cells[0].get_text(strip=True)
                values = {
                    header_cells[i + 1]: cells[i].get_text(strip=True) for i in range(len(cells))
                }
            else:
                continue

            if label == "Totals":
                team_stats.append(ScrapedTeamMatchStats(
                    team_name=team_name,
                    stats={
                        field_name: _parse_int_cell(values.get(col, ""))
                        for col, field_name in STAT_COLUMN_MAP.items()
                    },
                ))
            elif label == "Opposition":
                continue
            else:
                player_link = cells[1].find("a")
                pct_text = values.get("%P", "")
                player_stats.append(ScrapedPlayerStat(
                    team_name=team_name,
                    player_name=label,
                    player_url=player_link["href"] if player_link is not None else None,
                    stats={
                        field_name: _parse_int_cell(values.get(col, ""))
                        for col, field_name in STAT_COLUMN_MAP.items()
                    },
                    time_on_ground_pct=float(pct_text) if pct_text.replace(".", "", 1).isdigit() else None,
                ))

    return team_stats, player_stats
