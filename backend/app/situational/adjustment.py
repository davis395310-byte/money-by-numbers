"""Situational Elo adjustment (Phase 2) — exactly ONE wired adjustment.

Research verdict (docs/IN_SEASON_RECALIBRATION.md, 2016-2025 walk-forward,
n=2639 regular-season games, no leakage):

- DEAD_RUBBER favorites: 2-6 actual vs 0.713 Elo-implied (n=8, diff -0.463,
  z=-2.90, same sign in both halves; 6 of 8 are independently legible
  rest-starters games). INCLUDE — the only situational effect that earned it.
- ELIMINATED teams: no measurable underperformance vs Elo (dog eliminated
  z=-0.88; favorite-eliminated excess vs baseline z=-1.2). EXCLUDED.
- MUST_WIN (either side): no measurable urgency effect (z=-0.99 / -2.17,
  the latter better than baseline). EXCLUDED.

The discount is HEAVILY shrunk because n=8: raw MLE ≈ 349 Elo points
(logit gap 2.01 × 400/ln10), shrunk with a skeptical prior of weight 48
(6× the data mass) at zero::

    DEAD_RUBBER_DISCOUNT_ELO = 349 × 8/(8+48) ≈ 50

Re-estimate when more dead-rubber games accrue; the 4-week re-check job
(``pipelines/jobs/recalibrate.py``) owns this number and must beat the
incumbent on held-out recent games by the guardrail margin to change it.

Every adjusted pick is tagged ``"situational-adjustment"`` — never silent.
The tag, the team, its stakes, and the Elo shift travel on the prediction
document (``adjustments`` / ``adjustment_detail``) and into API responses.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .stakes import STAKES

#: Master switch. False disables every situational shift (tags empty).
ADJUSTMENT_ENABLED = True

#: Tag applied to every adjusted pick. Never silent.
SITUATIONAL_TAG = "situational-adjustment"

# --- derivation (see module docstring) ---
_DEAD_RUBBER_RAW_MLE_ELO = 349.0  # logit gap 2.01 × 400/ln(10), n=8, 2016-2025
_DEAD_RUBBER_N = 8
_DEAD_RUBBER_PRIOR_WEIGHT = 48.0  # skeptical prior: 6× the data mass at 0.0

#: Shrunken dead-rubber Elo discount applied to the DEAD_RUBBER team.
DEAD_RUBBER_DISCOUNT_ELO: float = round(
    _DEAD_RUBBER_RAW_MLE_ELO
    * (_DEAD_RUBBER_N / (_DEAD_RUBBER_N + _DEAD_RUBBER_PRIOR_WEIGHT)),
    0,
)  # = 50.0

#: Standard Elo scale / home field used when converting shifts to probabilities.
ELO_SCALE = 400.0
HFA_ELO = 35.0


def situational_shifts(
    stakes_home: str,
    stakes_away: str,
) -> Dict[str, Any]:
    """Elo-point shifts for a game from both teams' stakes.

    Returns ``{"shift_home","shift_away","tags","detail"}``. Only
    DEAD_RUBBER triggers a shift (``-DEAD_RUBBER_DISCOUNT_ELO`` on that
    team); every other stakes value yields exactly 0.0. ``tags`` contains
    ``"situational-adjustment"`` iff any shift is nonzero, and ``detail``
    records which team, why, and how much — the audit trail that travels
    with the pick.
    """
    if stakes_home not in STAKES or stakes_away not in STAKES:
        raise ValueError(
            f"stakes must be in {STAKES}, got {stakes_home!r}/{stakes_away!r}"
        )
    shift_home = 0.0
    shift_away = 0.0
    applied: List[Dict[str, Any]] = []
    if ADJUSTMENT_ENABLED:
        if stakes_home == "DEAD_RUBBER":
            shift_home = -DEAD_RUBBER_DISCOUNT_ELO
            applied.append(
                {
                    "team_side": "home",
                    "stakes": stakes_home,
                    "elo_shift": shift_home,
                    "basis": (
                        f"dead-rubber discount {DEAD_RUBBER_DISCOUNT_ELO} "
                        f"(n={_DEAD_RUBBER_N} games, shrunk; see "
                        "docs/IN_SEASON_RECALIBRATION.md)"
                    ),
                }
            )
        if stakes_away == "DEAD_RUBBER":
            shift_away = -DEAD_RUBBER_DISCOUNT_ELO
            applied.append(
                {
                    "team_side": "away",
                    "stakes": stakes_away,
                    "elo_shift": shift_away,
                    "basis": (
                        f"dead-rubber discount {DEAD_RUBBER_DISCOUNT_ELO} "
                        f"(n={_DEAD_RUBBER_N} games, shrunk; see "
                        "docs/IN_SEASON_RECALIBRATION.md)"
                    ),
                }
            )
    tags = [SITUATIONAL_TAG] if applied else []
    return {
        "shift_home": shift_home,
        "shift_away": shift_away,
        "tags": tags,
        "detail": {SITUATIONAL_TAG: applied} if applied else {},
    }


def adjusted_win_probability(
    elo_home: float,
    elo_away: float,
    shift_home: float = 0.0,
    shift_away: float = 0.0,
    hfa_elo: float = HFA_ELO,
) -> float:
    """Home win probability after situational Elo shifts.

    ``p = 1 / (1 + 10^(-((eh+sh) - (ea+sa) + hfa) / 400))``. With zero
    shifts this is exactly the standard Elo logistic — the adjustment can
    only ever move the number via an explicit, tagged shift.
    """
    eh = float(elo_home) + float(shift_home)
    ea = float(elo_away) + float(shift_away)
    return 1.0 / (1.0 + 10.0 ** (-((eh - ea) + float(hfa_elo)) / ELO_SCALE))


def logit(p: float) -> float:
    p = min(max(float(p), 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-float(x)))
