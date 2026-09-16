"""Immutable pick ledger (Phase 6, spec section 37).

``lock_pick`` is the ONLY sanctioned writer of the ``predictions``
collection. It enforces three invariants:

1. **Pre-kickoff only** — a pick is refused when ``kickoff`` is missing or
   already past. No backfilling.
2. **Insert-only** — one published pick per (game_id, model_version,
   market). A second write for the same triple is refused, never an
   overwrite.
3. **Schema-validated** — every row passes the ``Prediction`` pydantic
   model before it reaches MongoDB.

There are no update/delete endpoints for ``predictions`` anywhere in the
API (see ``backend/app/routers/``), so once a row is locked it can only be
read or graded — never edited or removed. Losing picks stay on the public
record permanently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..models.predictions import Prediction


class PickLedgerError(Exception):
    """Raised when a pick cannot be locked (late, duplicate, invalid)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def lock_pick(
    db: Any,
    *,
    prediction_id: str,
    game_id: str,
    model_version: str,
    training_cutoff: str,
    model_probability: float,
    predicted_winner: str,
    kickoff: datetime,
    season: Optional[int] = None,
    week: Optional[int] = None,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    predicted_home_score: Optional[int] = None,
    predicted_away_score: Optional[int] = None,
    market: Optional[str] = None,
    side: Optional[str] = None,
    market_line_at_pick: Optional[float] = None,
    market_price_at_pick: Optional[int] = None,
    sportsbook: Optional[str] = None,
    market_probability: Optional[float] = None,
    edge: Optional[float] = None,
    confidence: Optional[str] = None,
    adjustments: Optional[list] = None,
    adjustment_detail: Optional[dict] = None,
) -> Dict[str, Any]:
    """Lock one published pick into the immutable ``predictions`` ledger.

    Raises :class:`PickLedgerError` when:
    - ``kickoff`` is missing/naive-unusable or already past (no backfilling),
    - a pick for (game_id, model_version, market) already exists,
    - ``prediction_id`` already exists,
    - the row fails ``Prediction`` schema validation.
    """
    if kickoff is None:
        raise PickLedgerError(
            "refusing to lock pick without a kickoff timestamp: "
            "pre-kickoff publication cannot be verified"
        )
    kickoff_utc = _as_utc(kickoff)
    if kickoff_utc <= utcnow():
        raise PickLedgerError(
            f"refusing to lock pick for game {game_id}: kickoff "
            f"({kickoff_utc.isoformat()}) is not in the future"
        )

    market_key = market or "moneyline"
    existing = db["predictions"].find_one(
        {"game_id": game_id, "model_version": model_version, "market": market_key}
    )
    if existing is not None:
        raise PickLedgerError(
            f"duplicate pick refused: (game_id={game_id}, "
            f"model_version={model_version}, market={market_key}) already locked "
            f"as {existing.get('prediction_id')}"
        )
    if db["predictions"].find_one({"prediction_id": prediction_id}) is not None:
        raise PickLedgerError(
            f"duplicate prediction_id refused: {prediction_id} already locked"
        )

    if confidence is None:
        # Same tiers as the edge engine (backend/app/edges/engine.py).
        p = float(model_probability)
        confidence = "high" if p >= 0.68 else ("medium" if p >= 0.58 else "low")

    doc = Prediction(
        prediction_id=prediction_id,
        game_id=game_id,
        created_at=utcnow(),
        model_version=model_version,
        training_cutoff=training_cutoff,
        season=season,
        week=week,
        home_team=home_team,
        away_team=away_team,
        kickoff=kickoff_utc,
        model_probability=model_probability,
        predicted_winner=predicted_winner,
        predicted_home_score=predicted_home_score,
        predicted_away_score=predicted_away_score,
        market=market_key,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        market_line_at_pick=market_line_at_pick,
        market_price_at_pick=market_price_at_pick,
        sportsbook=sportsbook,
        market_probability=market_probability,
        edge=edge,
        confidence=confidence,  # type: ignore[arg-type]
        adjustments=adjustments,
        adjustment_detail=adjustment_detail,
    ).model_dump()

    db["predictions"].insert_one(doc)
    return doc


def get_ledger_pick(db: Any, prediction_id: str) -> Optional[Dict[str, Any]]:
    """Read one locked pick by id (reads never mutate)."""
    doc = db["predictions"].find_one({"prediction_id": prediction_id}, {"_id": 0})
    return doc
