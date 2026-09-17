"""Per-pick tipping-point explainers (paid-tier feature).

A locked prediction says *who* the model picked and *how likely* the win
is. The explainer says *why* — the tipping points behind the pick — in two
or three plain sentences.

Honesty contract (this module NEVER invents a reason):

- Every clause maps to a real input of the pick. The champion
  (MNB-NFL-2026.01) is pure Elo, so the only tipping points it can cite
  are: the pre-game rating gap, the home-field expectation adjustment,
  and rest differential. Injuries, weather, "momentum", and matchup
  narratives are NOT model inputs and are never mentioned.
- The rating gap is recovered *from the locked probability itself* via
  the champion formula ``p = elo_expected(elo_diff + 35)``, so the
  explainer can never contradict the published number. A replay of the
  walk-forward Elo (``ml.tipping_points``) confirms the gaps to within a
  few rating points; the implied gap is what the pick actually encodes.
- Market comparison appears only when a real odds snapshot exists for
  the game, and is purely descriptive ("the books price X at N%") —
  never a betting recommendation.

Explainers are stored in the ``pick_explainers`` collection keyed by
``prediction_id``. The immutable ``predictions`` ledger is never written
to; the API joins them at read time for requesters with paid access.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..data_providers.teams_data import TEAMS

# Must match ml.features.ELO_HFA / ELO_SCALE (champion .01 constants).
_ELO_SCALE = 400.0
_ELO_HFA = 35.0

GENERATOR_VERSION = "explainer-v1"

_TEAM_LABEL = {t["abbr"]: f"{t['city']} {t['name']}" for t in TEAMS}
# Locked picks predate the canonical map for one franchise.
_TEAM_LABEL["LA"] = _TEAM_LABEL["LAR"]


def team_label(abbr: Optional[str]) -> str:
    if not abbr:
        return "Unknown team"
    return _TEAM_LABEL.get(abbr.upper(), abbr.upper())


def possessive(name: str) -> str:
    """Rams' — not Rams's."""
    return name + "'" if name.endswith(("s", "S")) else name + "'s"


def implied_rating_gap(model_prob_home: float) -> float:
    """Rating gap (home minus away, pre-HFA) the locked probability encodes."""
    p = min(max(model_prob_home, 1e-6), 1 - 1e-6)
    return -_ELO_SCALE * math.log10(1.0 / p - 1.0) - _ELO_HFA


def _moneyline_to_prob(ml: float) -> float:
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def _confidence_word(confidence: Any) -> str:
    c = str(confidence or "").strip().lower()
    return {"high": "high", "medium": "medium", "low": "low"}.get(c, "medium")


def build_explainer(
    prediction: Dict[str, Any],
    rest_diff: int = 0,
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the tipping-point explainer for one locked prediction.

    ``prediction`` needs: predicted_winner, home_team, away_team,
    model_probability, confidence. ``rest_diff`` is home-minus-away days
    of rest from the schedule. ``market`` (optional) carries
    ``moneyline_home`` / ``moneyline_away`` from a real odds snapshot.
    """
    winner = str(prediction.get("predicted_winner") or "").upper()
    home = str(prediction.get("home_team") or "").upper()
    away = str(prediction.get("away_team") or "").upper()
    prob = float(prediction.get("model_probability") or 0.5)
    confidence = _confidence_word(prediction.get("confidence"))
    model_version = prediction.get("model_version") or "MNB-NFL-2026.01"

    pick_is_home = winner == home or (home == "LAR" and winner == "LA")
    # Probability from the picked side's perspective.
    p_home = prob if pick_is_home else 1.0 - prob
    gap = implied_rating_gap(p_home)  # home minus away, pre-HFA
    pick_gap = gap if pick_is_home else -gap  # gap in the pick's favor

    pick_name = team_label(winner)
    opp_abbr = away if pick_is_home else home
    opp_name = team_label(opp_abbr)
    pick_poss = possessive(pick_name)
    opp_poss = possessive(opp_name)
    prob_pct = f"{prob * 100:.1f}%"

    sentences: List[str] = [
        f"The model takes {pick_name} at {prob_pct} ({confidence} confidence)."
    ]
    tipping_points: List[Dict[str, Any]] = [
        {
            "factor": "rating_gap",
            "label": "Pre-game rating gap",
            "detail": (
                f"{abs(round(pick_gap)):d} Elo points in {pick_poss} favor "
                f"(home minus away, before the home-field adjustment)"
                if pick_gap >= 0
                else f"{abs(round(pick_gap)):d} Elo points against {pick_name} "
                f"(home minus away, before the home-field adjustment)"
            ),
            "value": round(pick_gap, 1),
        }
    ]

    # --- Sentence 2: the tipping points ---
    gap_pts = abs(round(pick_gap))
    close_game = abs(p_home - 0.5) < 0.03
    if pick_is_home:
        if close_game:
            s2 = (
                f"This one is close to a coin flip — a {gap_pts}-point rating "
                f"gap, with home field tipping it to {pick_name}."
            )
        elif pick_gap < 0:
            s2 = (
                f"The tipping point is home field itself — the rating gap "
                f"actually favors {opp_name}, but the home edge tips this "
                f"one to {pick_name}."
            )
        elif gap_pts < 80:
            s2 = (
                f"The tipping point is a {gap_pts}-point rating gap in "
                f"{pick_poss} favor, with home field adding the usual edge."
            )
        else:
            s2 = (
                f"The tipping point is a {gap_pts}-point rating gap — "
                f"{pick_name} rated far stronger — and home field only widens it."
            )
        tipping_points.append(
            {
                "factor": "home_field",
                "label": "Home-field adjustment",
                "detail": (
                    f"+{int(_ELO_HFA)} points applied to the expectation only, "
                    f"in {pick_poss} favor"
                ),
                "value": _ELO_HFA,
            }
        )
    else:
        s2 = (
            f"The tipping point is a {gap_pts}-point rating gap for {pick_name} "
            f"— big enough to overcome {opp_poss} home-field edge."
        )
        tipping_points.append(
            {
                "factor": "home_field_overcome",
                "label": "Home-field adjustment overcome",
                "detail": (
                    f"+{int(_ELO_HFA)}-point home expectation for {opp_name} "
                    f"was not enough to offset the rating gap"
                ),
                "value": -_ELO_HFA,
            }
        )

    if rest_diff != 0:
        edge_side = pick_name if (rest_diff > 0) == pick_is_home else opp_name
        s2 += f" {edge_side} also holds a {abs(rest_diff)}-day rest edge."
        tipping_points.append(
            {
                "factor": "rest",
                "label": "Rest differential",
                "detail": f"{abs(rest_diff)} extra days of rest for {edge_side}",
                "value": rest_diff,
            }
        )
    sentences.append(s2)

    # --- Sentence 3: market comparison (only with a real snapshot) ---
    market_note: Optional[Dict[str, Any]] = None
    if market:
        ml_pick = market.get("moneyline_home") if pick_is_home else market.get(
            "moneyline_away"
        )
        if ml_pick:
            mkt_prob = _moneyline_to_prob(float(ml_pick))
            diff_pp = (prob - mkt_prob) * 100
            if abs(diff_pp) < 3:
                s3 = (
                    f"The market agrees — books price {pick_name} at "
                    f"{mkt_prob * 100:.0f}%, right in line with the model."
                )
            elif diff_pp > 0:
                s3 = (
                    f"The market is cooler on this one: books price {pick_name} "
                    f"at {mkt_prob * 100:.0f}% versus the model's {prob_pct}."
                )
            else:
                s3 = (
                    f"The market is warmer on this one: books price {pick_name} "
                    f"at {mkt_prob * 100:.0f}% versus the model's {prob_pct}."
                )
            sentences.append(s3)
            market_note = {
                "factor": "market",
                "label": "Market comparison",
                "detail": (
                    f"Model {prob_pct} vs market {mkt_prob * 100:.0f}% "
                    f"for {pick_name} (snapshot {market.get('snapshot_at')})"
                ),
                "value": round(diff_pp, 1),
            }
            tipping_points.append(market_note)

    return {
        "prediction_id": prediction.get("prediction_id"),
        "model_version": model_version,
        "explainer": " ".join(sentences),
        "tipping_points": tipping_points,
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Paid-tier gating
# ---------------------------------------------------------------------------

_PAID_RANKS = ("starter", "pro", "agency")


def paid_access_granted(
    db: Any, request: Any, year_one_free: bool
) -> tuple[bool, str]:
    """Whether this requester may see tipping-point explainers.

    Granted to paid plans (starter/pro/agency), and to everyone while
    ``year_one_free`` is on — the founder's year-one-free decision means
    paid features are free for the first year. Returns (granted, reason).
    """
    if year_one_free:
        return True, "year_one_free"
    try:
        from ..billing.identity import get_plan, resolve_requester

        user_key = resolve_requester(request)
        plan = get_plan(db, user_key)
    except Exception:
        return False, "identity_unavailable"
    if plan in _PAID_RANKS:
        return True, f"plan:{plan}"
    return False, "free_plan"
