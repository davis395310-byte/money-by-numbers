"""Prediction endpoints (Phase 3, spec sections 52/70-73).

- ``GET /api/predictions`` — latest stored predictions (query: season,
  week, limit). Every row carries ``model_version`` + ``training_cutoff``.
  Pass ``include_explainer=true`` to also get the paid-tier tipping-point
  explainer joined onto each pick (paid plans, or everyone while
  YEAR_ONE_FREE is on; otherwise the join is skipped and the response
  says why).
- ``GET /api/predictions/{prediction_id}/explainer`` — the tipping-point
  explainer for one locked pick. Paid-tier gated: 402 when the requester
  is on the free plan after year one ends.
- ``GET /api/predictions/models`` — registered model versions.
- ``GET /api/predictions/backtests`` — recorded backtest runs.

Honest empty states: when MongoDB is not configured, or when no
predictions have been stored yet, the endpoints return empty lists with
``"status": "no_predictions"`` — never fabricated picks. Predictions are
only ever written by a real training run (see ``ml/train.py``); nothing
here invents them.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request

from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..picks.explainers import paid_access_granted

router = APIRouter(tags=["predictions"])

_PUBLIC_PREDICTION_FIELDS = {
    "prediction_id": 1,
    "game_id": 1,
    "season": 1,
    "week": 1,
    "home_team": 1,
    "away_team": 1,
    "model_version": 1,
    "training_cutoff": 1,
    "model_probability": 1,
    "predicted_winner": 1,
    "predicted_home_score": 1,
    "predicted_away_score": 1,
    "confidence": 1,
    "created_at": 1,
    "actual_winner": 1,
    "correct": 1,
    "adjustments": 1,
    "adjustment_detail": 1,
    "_id": 0,
}


def _db() -> Any | None:
    if not get_settings().MONGODB_URI:
        return None
    try:
        client = get_mongo_client()
    except Exception:
        return None
    if client is None:
        return None
    return client[get_database_name()]


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


@router.get("/api/predictions")
def list_predictions(
    request: Request,
    season: Optional[int] = Query(default=None),
    week: Optional[int] = Query(default=None),
    limit: int = Query(default=32, ge=1, le=200),
    include_explainer: bool = Query(default=False),
) -> Dict[str, Any]:
    """Latest stored predictions, newest first."""
    db = _db()
    if db is None:
        return {"status": "no_predictions", "reason": "database not configured", "predictions": []}
    try:
        query: Dict[str, Any] = {}
        if season is not None:
            query["season"] = season
        if week is not None:
            query["week"] = week
        docs = list(
            db["predictions"]
            .find(query, _PUBLIC_PREDICTION_FIELDS)
            .sort("created_at", -1)
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001 - honest failure, not a crash
        return {"status": "error", "reason": str(exc)[:200], "predictions": []}
    if not docs:
        return {"status": "no_predictions", "reason": "no predictions stored yet", "predictions": []}
    out = {"status": "ok", "predictions": [_serialize(d) for d in docs]}

    # Paid-tier tipping-point explainers, joined at read time. The
    # immutable ledger is never modified; explainers live in their own
    # collection (see app/picks/explainers.py).
    if include_explainer:
        granted, reason = paid_access_granted(
            db, request, get_settings().YEAR_ONE_FREE
        )
        out["explainer_access"] = {"granted": granted, "reason": reason}
        if granted:
            ids = [d.get("prediction_id") for d in docs]
            joined = {
                e["prediction_id"]: e
                for e in db["pick_explainers"].find(
                    {"prediction_id": {"$in": ids}}, {"_id": 0}
                )
            }
            for p in out["predictions"]:
                exp = joined.get(p.get("prediction_id"))
                p["explainer"] = exp["explainer"] if exp else None
                p["tipping_points"] = exp["tipping_points"] if exp else []
    return out


@router.get("/api/predictions/{prediction_id}/explainer")
def get_explainer(prediction_id: str, request: Request) -> Dict[str, Any]:
    """Tipping-point explainer for one locked pick (paid-tier gated)."""
    db = _db()
    if db is None:
        return {"status": "no_explainer", "reason": "database not configured"}
    granted, reason = paid_access_granted(db, request, get_settings().YEAR_ONE_FREE)
    if not granted:
        return {
            "status": "gated",
            "reason": (
                "Tipping-point explainers are a paid-tier feature "
                f"(access check: {reason})."
            ),
            "upgrade_url": "/pricing",
        }
    try:
        doc = db["pick_explainers"].find_one(
            {"prediction_id": prediction_id}, {"_id": 0}
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200]}
    if not doc:
        return {
            "status": "no_explainer",
            "reason": "no explainer generated for this pick yet",
        }
    return {"status": "ok", **_serialize(doc)}


@router.get("/api/predictions/models")
def list_models() -> Dict[str, Any]:
    """Registered model versions (``MNB-NFL-YYYY.NN``)."""
    db = _db()
    if db is None:
        return {"status": "no_models", "reason": "database not configured", "models": []}
    try:
        docs = list(db["models"].find({}, {"_id": 0}).sort("created_at", -1))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "models": []}
    if not docs:
        return {"status": "no_models", "reason": "no model versions registered yet", "models": []}
    return {"status": "ok", "models": [_serialize(d) for d in docs]}


@router.get("/api/predictions/backtests")
def list_backtests() -> Dict[str, Any]:
    """Recorded backtest runs with their real measured metrics."""
    db = _db()
    if db is None:
        return {"status": "no_backtests", "reason": "database not configured", "backtests": []}
    try:
        docs = list(db["backtests"].find({}, {"_id": 0}).sort("created_at", -1))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "backtests": []}
    if not docs:
        return {"status": "no_backtests", "reason": "no backtests recorded yet", "backtests": []}
    return {"status": "ok", "backtests": [_serialize(d) for d in docs]}
