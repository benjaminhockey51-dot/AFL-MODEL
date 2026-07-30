from __future__ import annotations

import responses

from afl_model.data.sources.squiggle import SquiggleClient


@responses.activate
def test_get_teams_sends_descriptive_user_agent_and_parses_response():
    responses.add(
        responses.GET,
        "https://api.squiggle.com.au/",
        json={"teams": [{"id": 1, "name": "Adelaide", "abbrev": "ADE"}]},
        status=200,
    )

    client = SquiggleClient()
    teams = client.get_teams()

    assert teams == [{"id": 1, "name": "Adelaide", "abbrev": "ADE"}]
    sent_request = responses.calls[0].request
    assert sent_request.params["q"] == "teams"
    assert "afl-model" in sent_request.headers["User-Agent"]


@responses.activate
def test_get_games_passes_year_and_round():
    responses.add(
        responses.GET,
        "https://api.squiggle.com.au/",
        json={"games": [{"id": 372, "year": 2018, "round": 1}]},
        status=200,
    )

    client = SquiggleClient()
    games = client.get_games(year=2018, round_number=1)

    assert games == [{"id": 372, "year": 2018, "round": 1}]
    sent_params = responses.calls[0].request.params
    assert sent_params["year"] == "2018"
    assert sent_params["round"] == "1"


@responses.activate
def test_rate_limiter_enforces_minimum_interval(monkeypatch):
    responses.add(
        responses.GET, "https://api.squiggle.com.au/", json={"teams": []}, status=200
    )
    responses.add(
        responses.GET, "https://api.squiggle.com.au/", json={"teams": []}, status=200
    )

    clock = {"t": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    client = SquiggleClient()
    client.get_teams()
    clock["t"] += 0.2  # well under the configured minimum interval
    client.get_teams()

    assert len(sleeps) == 1
    assert sleeps[0] > 0.5
