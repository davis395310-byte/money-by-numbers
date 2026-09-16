"""NFL playoff simulation (Monte Carlo bracket + remaining-season simulator).

Pure functions only: nothing here touches a database or invents data.
Seeds, ratings, records, and remaining games are all explicit caller
inputs; missing data raises a clear error instead of being guessed.

- ``bracket.playoff_format`` — 12-team (2016-2019) vs 14-team (2020+) rules.
- ``bracket.build_bracket_rounds`` — Wild Card matchups + bye teams, with
  the reseeding procedure documented for later rounds.
- ``bracket.apply_tiebreakers`` — official tiebreak order on explicit
  standings input, implemented through strength of schedule; anything
  beyond that is marked NOT VERIFIED / DATA UNAVAILABLE, never guessed.
- ``engine.win_probability`` — standard Elo logistic (scale 400).
- ``engine.simulate_playoff_bracket`` — Monte Carlo over an explicit
  seeded bracket.
- ``engine.simulate_remaining_season`` — Monte Carlo over explicit
  remaining games, then seeds, then one bracket realization per sim.
"""

from .bracket import (
    apply_tiebreakers,
    build_bracket_rounds,
    championship_matchup,
    divisional_matchups,
    playoff_format,
    wild_card_matchups,
)
from .engine import (
    DEFAULT_ADJUSTMENTS,
    MAX_SIMS,
    PlayoffSimError,
    resolve_adjustments,
    simulate_playoff_bracket,
    simulate_remaining_season,
    win_probability,
)

__all__ = [
    "DEFAULT_ADJUSTMENTS",
    "MAX_SIMS",
    "PlayoffSimError",
    "apply_tiebreakers",
    "build_bracket_rounds",
    "championship_matchup",
    "divisional_matchups",
    "playoff_format",
    "resolve_adjustments",
    "simulate_playoff_bracket",
    "simulate_remaining_season",
    "wild_card_matchups",
    "win_probability",
]
