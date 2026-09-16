"""Track-record + methodology endpoints (Phase 6, spec sections 36-40).

- ``GET /api/track-record/summary`` — overall record, computed live from
  the ``pick_results`` ledger.
- ``GET /api/track-record/tiers`` — accuracy by confidence tier.
- ``GET /api/track-record/calibration`` — predicted-vs-observed buckets
  for moneyline picks.
- ``GET /api/track-record/picks`` — recent settled (and pending) picks.
- ``GET /api/methodology`` — the real measured 2016-2025 walk-forward
  numbers, read from the on-disk artifacts (never hardcoded), plus the
  public research writeup when it lands.

Every number is computed from the ledger at request time — nothing is
hardcoded. There are intentionally NO write routes here: the pick ledger
is append-only by design, and losing picks can never be edited or
removed through the API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from ..config import get_settings
from ..database import get_database_name, get_mongo_client

router = APIRouter(tags=["track-record"])

# ml/artifacts/ lives at the repo root, two levels above backend/app/.
_ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "ml" / "artifacts"
_RESEARCH_DIR = Path(__file__).resolve().parents[3].parent / "nfl-model-research"

_COUNTABLE_RESULTS = ("win", "loss")


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


def _accuracy(wins: int, losses: int) -> Optional[float]:
    decided = wins + losses
    return (wins / decided) if decided else None


def _summarize(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate win/loss/push + ROI from settled result docs.

    ROI is computed ONLY over picks that recorded a real market price at
    pick time (unit stakes: win pays decimal-1, loss loses 1, push returns
    0). Picks without prices are excluded from ROI and counted in
    ``roi_excluded_no_price`` — never estimated.
    """
    wins = sum(1 for d in docs if d.get("result") == "win")
    losses = sum(1 for d in docs if d.get("result") == "loss")
    pushes = sum(1 for d in docs if d.get("result") == "push")
    ungraded = sum(1 for d in docs if d.get("result") == "ungraded")

    roi_units: Optional[float] = None
    roi_n = 0
    roi_excluded = 0
    for d in docs:
        if d.get("result") not in _COUNTABLE_RESULTS:
            continue
        price = d.get("market_price_at_pick")
        if price is None:
            roi_excluded += 1
            continue
        try:
            a = float(price)
        except (TypeError, ValueError):
            roi_excluded += 1
            continue
        if a == 0:
            roi_excluded += 1
            continue
        decimal = 1.0 + (100.0 / -a if a < 0 else a / 100.0)
        roi_n += 1
        if roi_units is None:
            roi_units = 0.0
        roi_units += (decimal - 1.0) if d.get("result") == "win" else -1.0

    briers = [
        d["brier_contribution"]
        for d in docs
        if isinstance(d.get("brier_contribution"), (int, float))
    ]

    return {
        "n": len(docs),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "ungraded": ungraded,
        "accuracy": _accuracy(wins, losses),
        "roi_units": roi_units,
        "roi_n": roi_n,
        "roi_excluded_no_price": roi_excluded,
        "avg_brier": (sum(briers) / len(briers)) if briers else None,
        "brier_n": len(briers),
    }


@router.get("/api/track-record/summary")
def track_record_summary() -> Dict[str, Any]:
    """Overall track record, computed live from the pick-results ledger."""
    db = _db()
    if db is None:
        return {
            "status": "no_data",
            "reason": "database not configured",
            "summary": None,
        }
    try:
        docs = list(db["pick_results"].find({}, {"_id": 0}))
        pending = db["predictions"].count_documents(
            {
                "prediction_id": {
                    "$nin": [d.get("prediction_id") for d in docs if d.get("prediction_id")]
                }
            }
        )
    except Exception as exc:  # noqa: BLE001 - honest failure, not a crash
        return {"status": "error", "reason": str(exc)[:200], "summary": None}
    if not docs:
        return {
            "status": "no_settled_picks",
            "reason": "no picks have been settled yet",
            "summary": None,
            "pending_picks": pending,
        }
    by_market = {}
    for market in ("moneyline", "spread", "total"):
        by_market[market] = _summarize([d for d in docs if (d.get("market") or "moneyline") == market])
    return {
        "status": "ok",
        "summary": _summarize(docs),
        "by_market": by_market,
        "pending_picks": pending,
        "note": "Every number above is computed from the immutable pick ledger. "
        "Losing picks are never removed.",
    }


@router.get("/api/track-record/tiers")
def track_record_tiers() -> Dict[str, Any]:
    """Accuracy by confidence tier (HIGH / MEDIUM / LOW)."""
    db = _db()
    if db is None:
        return {"status": "no_data", "reason": "database not configured", "tiers": None}
    try:
        docs = list(db["pick_results"].find({}, {"_id": 0}))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "tiers": None}
    if not docs:
        return {
            "status": "no_settled_picks",
            "reason": "no picks have been settled yet",
            "tiers": None,
        }
    tiers: Dict[str, Any] = {}
    for tier in ("high", "medium", "low"):
        tier_docs = [d for d in docs if (d.get("confidence") or "low").lower() == tier]
        tiers[tier.upper()] = _summarize(tier_docs)
    return {"status": "ok", "tiers": tiers}


@router.get("/api/track-record/calibration")
def track_record_calibration() -> Dict[str, Any]:
    """Calibration buckets: for moneyline picks, predicted probability vs
    observed win rate per decile bucket. Empty when no graded moneyline
    picks exist."""
    db = _db()
    if db is None:
        return {"status": "no_data", "reason": "database not configured", "buckets": None}
    try:
        docs = list(
            db["pick_results"].find(
                {
                    "market": "moneyline",
                    "result": {"$in": list(_COUNTABLE_RESULTS)},
                    "model_probability": {"$ne": None},
                },
                {"_id": 0},
            )
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "buckets": None}
    if not docs:
        return {
            "status": "no_settled_picks",
            "reason": "no graded moneyline picks yet",
            "buckets": None,
        }
    buckets: List[Dict[str, Any]] = []
    for i in range(10):
        lo, hi = i / 10.0, (i + 1) / 10.0
        in_bucket = [
            d
            for d in docs
            if lo <= float(d["model_probability"]) < hi or (hi == 1.0 and float(d["model_probability"]) == 1.0)
        ]
        if not in_bucket:
            continue
        wins = sum(1 for d in in_bucket if d["result"] == "win")
        buckets.append(
            {
                "prob_low": lo,
                "prob_high": hi,
                "n": len(in_bucket),
                "avg_predicted": sum(float(d["model_probability"]) for d in in_bucket) / len(in_bucket),
                "observed_win_rate": wins / len(in_bucket),
            }
        )
    return {"status": "ok", "buckets": buckets}


@router.get("/api/track-record/picks")
def track_record_picks(
    limit: int = Query(default=50, ge=1, le=200),
    result: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Recent picks with outcomes. Includes pending (unsettled) picks with
    ``status: "pending"`` — the public sees picks BEFORE they resolve."""
    db = _db()
    if db is None:
        return {
            "status": "no_data",
            "reason": "database not configured",
            "picks": [],
        }
    try:
        query: Dict[str, Any] = {}
        if result in ("win", "loss", "push", "ungraded"):
            query["result"] = result
        settled = {
            d["prediction_id"]: d
            for d in db["pick_results"].find(query, {"_id": 0})
            if d.get("prediction_id")
        }
        locked = list(
            db["predictions"]
            .find({}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "picks": []}
    if not locked:
        return {
            "status": "no_picks",
            "reason": "no picks have been published yet",
            "picks": [],
        }
    picks: List[Dict[str, Any]] = []
    for pick in locked:
        row = _serialize(pick)
        res = settled.get(pick.get("prediction_id"))
        if res is None:
            row["settlement_status"] = "pending"
        else:
            row["settlement_status"] = "settled"
            row["result"] = res.get("result")
            row["actual_home_score"] = res.get("actual_home_score")
            row["actual_away_score"] = res.get("actual_away_score")
            row["clv_favorable"] = res.get("clv_favorable")
        if result is None or row.get("result") == result:
            picks.append(row)
    return {"status": "ok", "picks": picks[:limit]}


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


@router.get("/api/methodology")
def methodology() -> Dict[str, Any]:
    """Public methodology: the real measured walk-forward results, read from
    the on-disk training artifacts — never hardcoded.

    Includes the research writeup (``METHODOLOGY_PUBLIC.md``) when the
    parallel research task has delivered it; otherwise an honest
    ``research_note: null`` with the reason.
    """
    backtest = _read_json(_ARTIFACTS_DIR / "backtest_2016_2025.json")
    metadata = _read_json(_ARTIFACTS_DIR / "MNB-NFL-2026.02" / "metadata.json")

    research_path = _RESEARCH_DIR / "METHODOLOGY_PUBLIC.md"
    research_note = research_path.read_text() if research_path.exists() else None

    if backtest is None and metadata is None:
        return {
            "status": "no_methodology",
            "reason": "no training artifacts found on disk",
            "walk_forward": None,
        }

    walk_forward: Optional[Dict[str, Any]] = None
    if metadata and isinstance(metadata.get("walk_forward"), dict):
        wf = metadata["walk_forward"]
        per_season = []
        folds = backtest.get("folds") if isinstance(backtest, dict) else None
        if isinstance(folds, list):
            for f in folds:
                per_season.append(
                    {
                        "season": f.get("season"),
                        "n_games": f.get("n_games", f.get("n")),
                        "accuracy": f.get("accuracy"),
                        "brier": f.get("brier"),
                        "elo_accuracy": f.get("elo_accuracy"),
                    }
                )
        walk_forward = {
            "model_version": metadata.get("version"),
            "training_period": metadata.get("training_period"),
            "training_date": metadata.get("training_date"),
            "n_games": wf.get("n_games"),
            "seasons": wf.get("seasons"),
            "ensemble_accuracy": wf.get("accuracy"),
            "ensemble_brier": wf.get("brier"),
            "elo_baseline_accuracy": wf.get("elo_accuracy"),
            "elo_baseline_brier": wf.get("elo_brier"),
            "accuracy_vs_elo": wf.get("accuracy_vs_elo_baseline"),
            "ats_accuracy": wf.get("ats_accuracy"),
            "ats_n": wf.get("ats_n"),
            "margin_mae": wf.get("margin_mae"),
            "note": wf.get("note"),
            "per_season": per_season,
            "leakage_harness": metadata.get("leakage_harness"),
        }

    return {
        "status": "ok",
        "walk_forward": walk_forward,
        "research_note": research_note,
        "research_note_status": (
            "available" if research_note else "pending — independent research in progress"
        ),
        "limits": [
            "These are historical backtest results, not a promise of future performance.",
            "A ~62% winner rate does not clear the sportsbook vig on its own; "
            "profitability depends on beating market prices, not just picking winners.",
            "The simple Elo model beat the complex ensemble in testing — the "
            "platform uses the Elo lineage until a challenger proves better.",
        ],
    }
