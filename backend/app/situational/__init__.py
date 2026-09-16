"""Situational / motivation layer (Phase 2).

The Elo core already updates team strength after every game — the model was
never "static". What Elo cannot see is *stakes*: whether a team has anything
to play for. This package adds that layer:

- :mod:`stakes` — pure-function stakes classifier (MUST_WIN / NORMAL /
  DEAD_RUBBER / ELIMINATED) from standings + remaining-schedule math.
  No future data; tiebreaks ignored (documented limitation).
- :mod:`adjustment` — the single evidence-backed situational adjustment
  (dead-rubber Elo discount), heavily shrunk, explicitly tagged.

Research basis: ``docs/IN_SEASON_RECALIBRATION.md``. Only the dead-rubber
discount earned inclusion (n=8, z=-2.90, stable sign, legible mechanism);
MUST_WIN / ELIMINATED / momentum-style effects were tested and excluded.
"""

from .adjustment import (
    ADJUSTMENT_ENABLED,
    DEAD_RUBBER_DISCOUNT_ELO,
    adjusted_win_probability,
    situational_shifts,
)
from .stakes import STAKES, classify_stakes

__all__ = [
    "STAKES",
    "classify_stakes",
    "ADJUSTMENT_ENABLED",
    "DEAD_RUBBER_DISCOUNT_ELO",
    "situational_shifts",
    "adjusted_win_probability",
]
