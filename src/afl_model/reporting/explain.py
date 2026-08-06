from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExplanationInputs:
    home_team: str
    away_team: str
    predicted_winner: str  # "home" or "away"
    home_win_probability: float
    predicted_margin: float
    home_elo: float
    away_elo: float
    home_attack: float
    away_attack: float
    home_defence: float
    away_defence: float
    home_travel_km: Optional[float]
    away_travel_km: Optional[float]
    travel_elo_scale_per_100km: float
    confidence: float


# Below this travel-difference threshold, the effect isn't worth a sentence
# even if the weight is nonzero — mirrors how a human would describe it.
_TRAVEL_MENTION_THRESHOLD_KM = 300.0


def _confidence_label(confidence: float) -> str:
    if confidence >= 60:
        return "high confidence"
    if confidence >= 30:
        return "moderate confidence"
    return "low confidence"


def explain_prediction(inputs: ExplanationInputs) -> str:
    """Builds a short, plain-English explanation from the actual numbers
    that drove this specific prediction — never a generic template filled
    with stats that didn't actually influence anything. In particular:
    form and rest are deliberately never mentioned here while their
    prediction-config weights are 0 (see config.yaml) — citing them as a
    reason would misattribute the prediction to something that had no
    effect on it.

    predicted_winner comes from win probability, not predicted_margin's
    sign (see prediction_math.compute_prediction) — the two are
    independently-fit signals allowed to disagree on which side they
    favour. The opening sentence only states a margin figure when the two
    agree; when they don't, it states the win pick alone and leaves the
    margin figure to the attack/defence sentence below, which already
    names its own (possibly different) side correctly — attributing
    predicted_margin's number to the win-probability winner regardless of
    its actual sign was a real bug here, not a style choice.
    """
    winner_name = inputs.home_team if inputs.predicted_winner == "home" else inputs.away_team
    win_pct = inputs.home_win_probability if inputs.predicted_winner == "home" else 1.0 - inputs.home_win_probability

    margin_agrees_with_winner = (
        (inputs.predicted_winner == "home" and inputs.predicted_margin >= 0)
        or (inputs.predicted_winner == "away" and inputs.predicted_margin <= 0)
    )
    if margin_agrees_with_winner:
        sentences = [
            f"{winner_name} favoured by {abs(inputs.predicted_margin):.1f} points "
            f"({win_pct:.0%} win probability, {_confidence_label(inputs.confidence)})."
        ]
    else:
        sentences = [
            f"{winner_name} favoured to win "
            f"({win_pct:.0%} win probability, {_confidence_label(inputs.confidence)})."
        ]

    elo_diff = inputs.home_elo - inputs.away_elo
    if abs(elo_diff) >= 1:
        elo_favours = inputs.home_team if elo_diff > 0 else inputs.away_team
        sentences.append(f"Elo rating edge of {abs(elo_diff):.0f} points favours {elo_favours}.")

    # predicted_margin = (home_attack - away_attack) + (away_defence - home_defence)
    # — see afl_model.models.prediction_math.compute_prediction.
    attack_edge = inputs.home_attack - inputs.away_attack
    defence_edge = inputs.away_defence - inputs.home_defence
    margin_driver = attack_edge + defence_edge
    if abs(margin_driver) >= 1:
        margin_favours = inputs.home_team if margin_driver > 0 else inputs.away_team
        sentences.append(f"Attack/defence ratings favour {margin_favours} by {abs(margin_driver):.1f} points.")

    if inputs.travel_elo_scale_per_100km != 0 and inputs.home_travel_km is not None and inputs.away_travel_km is not None:
        travel_diff = inputs.home_travel_km - inputs.away_travel_km
        if abs(travel_diff) >= _TRAVEL_MENTION_THRESHOLD_KM:
            travelled_more = inputs.home_team if travel_diff > 0 else inputs.away_team
            sentences.append(f"{travelled_more} has travelled {abs(travel_diff):.0f}km further to this match.")

    return " ".join(sentences)
