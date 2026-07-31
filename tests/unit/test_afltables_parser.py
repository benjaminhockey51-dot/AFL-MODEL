from __future__ import annotations

from pathlib import Path

from afl_model.data.sources.afl_tables_parser import parse_match_stats_page, parse_season_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "afltables"


def test_parse_season_page_extracts_all_207_games_for_2018():
    html = (FIXTURES / "2018_season.html").read_text(encoding="utf-8")
    games = parse_season_page(html, 2018)

    assert len(games) == 207
    assert sum(1 for g in games if g.is_final) == 9


def test_parse_season_page_round_1_richmond_v_carlton():
    html = (FIXTURES / "2018_season.html").read_text(encoding="utf-8")
    games = parse_season_page(html, 2018)

    game = games[0]
    assert game.round_name == "Round 1"
    assert game.round_number == 1
    assert not game.is_final
    assert game.home_team == "Richmond"
    assert game.away_team == "Carlton"
    assert (game.home_goals, game.home_behinds, game.home_points) == (17, 19, 121)
    assert (game.away_goals, game.away_behinds, game.away_points) == (15, 5, 95)
    assert game.venue_slug == "mcg"
    assert game.venue_name == "M.C.G."
    assert game.attendance == 90151
    assert game.match_stats_path == "../stats/games/2018/031420180322.html"


def test_parse_season_page_finals_round_numbers_match_squiggle_convention():
    html = (FIXTURES / "2018_season.html").read_text(encoding="utf-8")
    games = parse_season_page(html, 2018)

    finals = {g.round_name: g.round_number for g in games if g.is_final}
    assert finals == {
        "Qualifying Final": 24,
        "Elimination Final": 24,
        "Semi Final": 25,
        "Preliminary Final": 26,
        "Grand Final": 27,
    }

    grand_final = next(g for g in games if g.round_name == "Grand Final")
    assert grand_final.home_team == "West Coast"
    assert grand_final.home_points == 79
    assert grand_final.away_team == "Collingwood"
    assert grand_final.away_points == 74


def test_parse_match_stats_page_team_totals():
    html = (FIXTURES / "2018_round1_richmond_carlton_stats.html").read_text(encoding="utf-8")
    team_stats, _ = parse_match_stats_page(html)

    by_team = {t.team_name: t.stats for t in team_stats}
    assert set(by_team) == {"Richmond", "Carlton"}
    assert by_team["Richmond"]["kicks"] == 207
    assert by_team["Richmond"]["inside_50s"] == 71
    assert by_team["Carlton"]["contested_possessions"] == 152


def test_parse_match_stats_page_player_rows():
    html = (FIXTURES / "2018_round1_richmond_carlton_stats.html").read_text(encoding="utf-8")
    _, player_stats = parse_match_stats_page(html)

    astbury = next(p for p in player_stats if p.player_name == "Astbury, David")
    assert astbury.team_name == "Richmond"
    assert astbury.player_url == "../../players/D/David_Astbury.html"
    assert astbury.stats["kicks"] == 9
    assert astbury.stats["marks"] == 7
    assert astbury.stats["disposals"] == 16
    assert astbury.time_on_ground_pct == 100.0

    # 22 registered players for each team is the standard bench size.
    richmond_players = [p for p in player_stats if p.team_name == "Richmond"]
    assert len(richmond_players) == 22
