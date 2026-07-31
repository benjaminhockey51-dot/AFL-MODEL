from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttackDefenceConfig:
    k_factor: float
    starting_league_avg_score: float
    league_avg_score_ewma_alpha: float


@dataclass(frozen=True)
class AttackDefenceUpdate:
    home_attack: float
    home_defence: float
    away_attack: float
    away_defence: float
    league_avg_score: float


def expected_scores(
    home_attack: float, home_defence: float, away_attack: float, away_defence: float,
    league_avg_score: float,
) -> "tuple[float, float]":
    """defence_rating uses the same sign direction as attack_rating: positive
    means "concedes more than league average" (a *worse* defence), negative
    means "concedes less" (a *better* defence) — so it adds directly to the
    opponent's expected score, the same way attack_rating adds to a team's
    own expected score. (Not "subtract a good defence" — that reads
    naturally but gets the sign backwards; verified against
    update_attack_defence below, which needs the same convention to move
    ratings in the right direction.)
    """
    expected_home = league_avg_score + home_attack + away_defence
    expected_away = league_avg_score + away_attack + home_defence
    return expected_home, expected_away


def update_attack_defence(
    home_attack: float, home_defence: float, away_attack: float, away_defence: float,
    league_avg_score: float, home_score: int, away_score: int, config: AttackDefenceConfig,
) -> AttackDefenceUpdate:
    """One attack/defence update for a single completed match.

    Both the scoring team's attack rating and the conceding team's defence
    rating move off the *same* residual (actual minus expected score) —
    the home team scoring more than expected is simultaneously evidence
    the home attack is stronger and the away defence is weaker.
    """
    expected_home, expected_away = expected_scores(
        home_attack, home_defence, away_attack, away_defence, league_avg_score
    )

    home_residual = home_score - expected_home
    away_residual = away_score - expected_away

    new_home_attack = home_attack + config.k_factor * home_residual
    new_away_defence = away_defence + config.k_factor * home_residual
    new_away_attack = away_attack + config.k_factor * away_residual
    new_home_defence = home_defence + config.k_factor * away_residual

    new_league_avg = (
        league_avg_score
        + config.league_avg_score_ewma_alpha * ((home_score + away_score) / 2.0 - league_avg_score)
    )

    return AttackDefenceUpdate(
        home_attack=new_home_attack,
        home_defence=new_home_defence,
        away_attack=new_away_attack,
        away_defence=new_away_defence,
        league_avg_score=new_league_avg,
    )
