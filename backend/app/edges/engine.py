"""Pure edge-computation engine (Phase 4).

No-vig math, edge vs the market, expected value, confidence tiers, and
champion-model resolution. No function here opens a database connection or
invents data: missing prices/probabilities propagate as ``None`` and
produce no edge rows.

Champion policy (explicit, never silent): the walk-forward harness measured
the Elo lineage at 62.16% winner accuracy vs 59.47% for the MNB-NFL-2026.02
ensemble (see ``ml/artifacts/MNB-NFL-2026.02/metadata.json``), so the Elo
lineage stays champion until a future registration explicitly flips the
``champion`` flag in the ``models`` collection. :func:`resolve_champion_model`
reads that flag; :data:`CHAMPION_MODEL_VERSION` is only the fallback when no
champion doc exists.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

#: Fallback champion when no ``champion: true`` doc exists in ``models``.
#: The Elo lineage stays champion because walk-forward proved it beats the
#: MNB-NFL-2026.02 ensemble (62.16% vs 59.47% winner accuracy, 2016-2025).
CHAMPION_MODEL_VERSION = "MNB-NFL-2026.01"

#: Default minimum edge (model_prob - no-vig implied) for a side to be flagged.
DEFAULT_EDGE_THRESHOLD = 0.03

#: Confidence tiers on model probability.
CONFIDENCE_HIGH = 0.68
CONFIDENCE_MEDIUM = 0.58


def american_to_implied(price: Any) -> Optional[float]:
    """Convert an American-odds price to its raw implied probability.

    Negative prices: ``-a / (-a + 100)``; positive prices: ``100 / (a + 100)``.
    ``None``/missing/invalid prices return ``None`` — never an invented number.
    """
    if price is None:
        return None
    try:
        a = float(price)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a < 0:
        return -a / (-a + 100.0)
    return 100.0 / (a + 100.0)


def american_to_decimal(price: Any) -> Optional[float]:
    """Convert an American-odds price to decimal odds (stake-included payout).

    Returns ``None`` for missing/invalid prices.
    """
    if price is None:
        return None
    try:
        a = float(price)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a < 0:
        return 1.0 + 100.0 / (-a)
    return 1.0 + a / 100.0


def no_vig_probabilities(
    price_a: Any, price_b: Any
) -> Tuple[Optional[float], Optional[float]]:
    """Two-way no-vig probabilities: ``p_a = imp_a / (imp_a + imp_b)``.

    Returns ``(None, None)`` when either price is missing/invalid — the vig
    cannot be removed without both sides, so nothing is guessed.
    """
    imp_a = american_to_implied(price_a)
    imp_b = american_to_implied(price_b)
    if imp_a is None or imp_b is None:
        return (None, None)
    total = imp_a + imp_b
    if total <= 0:
        return (None, None)
    return (imp_a / total, imp_b / total)


def edge_vs_market(
    model_prob: Any, novig_implied: Any
) -> Optional[float]:
    """Edge of a side: ``model_prob - novig_implied_prob``.

    ``None`` when either input is missing.
    """
    if model_prob is None or novig_implied is None:
        return None
    try:
        return float(model_prob) - float(novig_implied)
    except (TypeError, ValueError):
        return None


def expected_value(model_prob: Any, price: Any) -> Optional[float]:
    """Expected value of a unit stake: ``model_prob * decimal_odds - 1``.

    ``None`` when the probability or price is missing/invalid.
    """
    if model_prob is None or price is None:
        return None
    try:
        p = float(model_prob)
    except (TypeError, ValueError):
        return None
    decimal_odds = american_to_decimal(price)
    if decimal_odds is None:
        return None
    return p * decimal_odds - 1.0


def confidence_tier(model_prob: Any) -> str:
    """Confidence tier for a model probability.

    ``HIGH`` >= 0.68, ``MEDIUM`` >= 0.58, else ``LOW``. Missing/invalid
    probabilities tier as ``LOW``.
    """
    try:
        p = float(model_prob)
    except (TypeError, ValueError):
        return "LOW"
    if p >= CONFIDENCE_HIGH:
        return "HIGH"
    if p >= CONFIDENCE_MEDIUM:
        return "MEDIUM"
    return "LOW"


def resolve_champion_model(db: Any) -> Dict[str, Any]:
    """Resolve which model version is champion.

    Reads the ``models`` collection for a doc with ``champion: true``. If
    none exists (or the read fails), falls back to
    :data:`CHAMPION_MODEL_VERSION` — the Elo lineage, which walk-forward
    proved beats the MNB-NFL-2026.02 ensemble. The fallback is explicit
    (``source: "fallback_constant"``), never a silent switch: a different
    version only becomes champion when a registration writes
    ``champion: true`` on it.
    """
    doc = None
    try:
        doc = db["models"].find_one({"champion": True}, {"_id": 0})
    except Exception:
        doc = None
    if isinstance(doc, Mapping) and doc.get("version"):
        return {
            "model_version": doc["version"],
            "source": "models_collection",
            "champion": True,
            "registered": dict(doc),
        }
    return {
        "model_version": CHAMPION_MODEL_VERSION,
        "source": "fallback_constant",
        "champion": None,
        "note": (
            "no document with champion:true in the models collection; "
            f"using explicit fallback {CHAMPION_MODEL_VERSION}"
        ),
    }


# (market, side) -> (snapshot price field, snapshot line field or None)
_MARKET_PRICE_FIELDS: Dict[Tuple[str, str], Tuple[str, Optional[str]]] = {
    ("moneyline", "home"): ("moneyline_home", None),
    ("moneyline", "away"): ("moneyline_away", None),
    ("spread", "home"): ("spread_home_price", "spread"),
    ("spread", "away"): ("spread_away_price", "spread"),
    ("total", "over"): ("total_over_price", "total"),
    ("total", "under"): ("total_under_price", "total"),
}


def build_edge_row(
    *,
    game_id: str,
    market: str,
    side: str,
    line: Any,
    price: Any,
    model_probability: Any,
    novig_implied: Any,
    model_version: str,
    threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """Build one edge row, or ``None`` when it should not be flagged.

    A row is flagged only when ``edge = model_probability - novig_implied``
    is computable and ``>= threshold``. Missing inputs yield ``None``.
    """
    edge = edge_vs_market(model_probability, novig_implied)
    if edge is None or edge < threshold:
        return None
    try:
        model_p = float(model_probability)
    except (TypeError, ValueError):
        return None
    return {
        "game_id": game_id,
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "model_probability": model_p,
        "novig_implied": float(novig_implied),
        "edge": edge,
        "ev": expected_value(model_p, price),
        "confidence": confidence_tier(model_p),
        "model_version": model_version,
    }


def compute_game_edges(
    game_id: str,
    snapshot: Mapping[str, Any],
    prediction: Mapping[str, Any],
    threshold: float = DEFAULT_EDGE_THRESHOLD,
    market_probs: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Compute flagged edge rows for one game.

    Moneyline edges come from the prediction's ``model_probability`` for its
    ``predicted_winner`` (the other side gets ``1 - model_probability``).
    Spread/total edges are computed only when ``market_probs`` supplies
    per-market model probabilities (keys like ``"spread:home"``,
    ``"total:over"``) — the stored prediction schema does not carry those,
    so no spread/total edge is ever invented from nothing.

    Every row labels the ``model_version`` actually used.
    """
    rows: List[Dict[str, Any]] = []
    model_version = prediction.get("model_version") or "unknown"
    model_prob = prediction.get("model_probability")
    predicted_winner = prediction.get("predicted_winner")
    home_team = prediction.get("home_team")
    away_team = prediction.get("away_team")

    # No-vig pairs per market: (home-ish key, away-ish key)
    market_pairs = [
        (("moneyline", "home"), ("moneyline", "away")),
        (("spread", "home"), ("spread", "away")),
        (("total", "over"), ("total", "under")),
    ]

    for (mkt_a, side_a), (mkt_b, side_b) in market_pairs:
        price_field_a, line_field_a = _MARKET_PRICE_FIELDS[(mkt_a, side_a)]
        price_field_b, _ = _MARKET_PRICE_FIELDS[(mkt_b, side_b)]
        price_a = snapshot.get(price_field_a)
        price_b = snapshot.get(price_field_b)
        novig_a, novig_b = no_vig_probabilities(price_a, price_b)
        if novig_a is None:
            continue
        line = snapshot.get(line_field_a) if line_field_a else None

        if mkt_a == "moneyline":
            # Map model probability onto home/away via predicted_winner.
            if model_prob is None or predicted_winner is None:
                continue
            if predicted_winner == home_team:
                probs = {side_a: model_prob, side_b: 1.0 - float(model_prob)}
            elif predicted_winner == away_team:
                probs = {side_a: 1.0 - float(model_prob), side_b: model_prob}
            else:
                continue
        else:
            if not market_probs:
                continue
            probs = {
                side_a: (market_probs or {}).get(f"{mkt_a}:{side_a}"),
                side_b: (market_probs or {}).get(f"{mkt_b}:{side_b}"),
            }

        for (mkt, side), price, novig in (
            ((mkt_a, side_a), price_a, novig_a),
            ((mkt_b, side_b), price_b, novig_b),
        ):
            row = build_edge_row(
                game_id=game_id,
                market=mkt,
                side=side,
                line=line,
                price=price,
                model_probability=probs[side],
                novig_implied=novig,
                model_version=model_version,
                threshold=threshold,
            )
            if row is not None:
                rows.append(row)
    return rows
