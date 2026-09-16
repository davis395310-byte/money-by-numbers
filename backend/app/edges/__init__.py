"""Edge engine (Phase 4): no-vig math, edge/EV, confidence tiers, champion resolution.

Everything here is pure and unit-testable. The only function that touches
a database is :func:`resolve_champion_model`, which takes an already-open
database handle as an argument (it never opens a connection itself).
"""

from .engine import (
    CHAMPION_MODEL_VERSION,
    DEFAULT_EDGE_THRESHOLD,
    american_to_decimal,
    american_to_implied,
    build_edge_row,
    compute_game_edges,
    confidence_tier,
    edge_vs_market,
    expected_value,
    no_vig_probabilities,
    resolve_champion_model,
)

__all__ = [
    "CHAMPION_MODEL_VERSION",
    "DEFAULT_EDGE_THRESHOLD",
    "american_to_decimal",
    "american_to_implied",
    "build_edge_row",
    "compute_game_edges",
    "confidence_tier",
    "edge_vs_market",
    "expected_value",
    "no_vig_probabilities",
    "resolve_champion_model",
]
