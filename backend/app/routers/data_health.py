"""GET /api/data/health — honest data-ingestion health (spec section 16).

Returns real collection counts when MongoDB is configured; otherwise every
dataset reports "not_yet_ingested" with zero counts. Freshness and recent
errors come from the ``ingestion_runs`` collection written by the ingestion
pipeline. Never crashes and never invents numbers: any failure keeps an
honest zero/error entry.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from ..config import get_settings
from ..database import get_database_name, get_mongo_client

router = APIRouter(tags=["data-health"])

DATASETS = {
    "teams": "teams",
    "games": "games",
    "game_stats": "game_stats",
    "injuries": "injuries",
    "odds": "odds",
    "weather": "weather",
}

# Timestamp-ish fields to probe for the most recent ingestion, in order.
TIMESTAMP_FIELDS = ("ingested_at", "timestamp", "created_at", "updated_at")

# Freshness TTL per dataset, in hours. A dataset whose last successful
# ingestion is older than its TTL is reported "stale".
FRESHNESS_TTL_HOURS = {
    "teams": 30 * 24,  # static reference data; monthly refresh is plenty
    "games": 7 * 24,
    "game_stats": 7 * 24,
    "injuries": 7 * 24,
    "odds": 48,
    "weather": 7 * 24,
}

MAX_REPORTED_RUN_ERRORS = 10


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Any) -> Optional[datetime]:
    """Return a timezone-aware datetime.

    MongoDB drivers (pymongo, mongomock) store datetimes as naive UTC, so
    values read back must be re-anchored before arithmetic.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _empty_state() -> Dict[str, Any]:
    return {
        "status": "not_yet_ingested",
        "last_ingestion": None,
        "record_count": 0,
        "freshness": None,
        "last_run_id": None,
    }


def _dataset_state(db: Any, collection_name: str) -> Dict[str, Any]:
    state = _empty_state()
    try:
        count = db[collection_name].count_documents({})
    except Exception as exc:  # noqa: BLE001 - reported honestly in errors
        return {"error": f"count_documents failed: {str(exc)[:200]}", **state}
    state["record_count"] = count
    if count == 0:
        return state
    state["status"] = "ok"
    # Find the newest document's timestamp-ish field for last_ingestion.
    try:
        for field in TIMESTAMP_FIELDS:
            doc = db[collection_name].find_one(
                {field: {"$exists": True}}, sort=[(field, -1)], projection={field: 1}
            )
            if doc and isinstance(doc.get(field), datetime):
                state["last_ingestion"] = doc[field].isoformat()
                break
    except Exception:
        pass
    return state


def _ingestion_run_state(db: Any) -> Dict[str, Any]:
    """Freshness + recent errors derived from the ingestion_runs collection.

    Freshness is dataset-specific: a dataset's ``last_run`` is the most
    recent run whose ``records_accepted`` includes that dataset. A games
    run never makes injuries look fresh, and vice versa.
    """
    info: Dict[str, Any] = {
        "last_run": None,
        "freshness": None,
        "recent_errors": [],
        "dataset_runs": {},
    }
    try:
        runs = list(db["ingestion_runs"].find().sort("finished_at", -1).limit(50))
    except Exception:
        return info
    if not runs:
        return info
    newest = runs[0]
    finished = newest.get("finished_at")
    info["last_run"] = {
        "run_id": newest.get("run_id"),
        "source": newest.get("source"),
        "status": newest.get("status"),
        "finished_at": finished.isoformat() if isinstance(finished, datetime) else None,
        "records_accepted": newest.get("records_accepted"),
        "records_rejected": newest.get("records_rejected"),
    }
    if isinstance(finished, datetime):
        finished_aware = _as_aware(finished)
        age_hours = (_utcnow() - finished_aware).total_seconds() / 3600.0
        info["freshness"] = "fresh" if age_hours <= FRESHNESS_TTL_HOURS["games"] else "stale"
    info["recent_errors"] = (newest.get("errors") or [])[:MAX_REPORTED_RUN_ERRORS]
    # Per-dataset: most recent run that accepted records for that dataset.
    for run in runs:
        accepted = run.get("records_accepted") or {}
        for name in DATASETS:
            if name in info["dataset_runs"] or name not in accepted:
                continue
            run_finished = run.get("finished_at")
            info["dataset_runs"][name] = {
                "run_id": run.get("run_id"),
                "source": run.get("source"),
                "status": run.get("status"),
                "accepted": accepted.get(name),
                "finished_at": (
                    run_finished.isoformat() if isinstance(run_finished, datetime) else None
                ),
            }
    return info


def _apply_run_info(states: Dict[str, Any], run_info: Dict[str, Any]) -> None:
    dataset_runs = run_info.get("dataset_runs", {})
    for name in DATASETS:
        state = states[name]
        own_run = dataset_runs.get(name)
        if own_run:
            # Only a run that actually ingested this dataset claims it.
            state["last_run_id"] = own_run["run_id"]
        if state["record_count"] > 0:
            # Freshness prefers the dataset's own document timestamps; the
            # dataset's own run finished_at is only a fallback. An unrelated
            # dataset's run must never make this one look fresh.
            last_ing = state.get("last_ingestion")
            try:
                ref = _as_aware(datetime.fromisoformat(last_ing)) if last_ing else None
            except ValueError:
                ref = None
            if ref is None and own_run and own_run.get("finished_at"):
                try:
                    ref = _as_aware(datetime.fromisoformat(own_run["finished_at"]))
                except ValueError:
                    ref = None
            if ref is not None:
                age_hours = (_utcnow() - ref).total_seconds() / 3600.0
                ttl = FRESHNESS_TTL_HOURS.get(name, 7 * 24)
                state["freshness"] = "fresh" if age_hours <= ttl else "stale"


@router.get("/api/data/health")
def data_health() -> Dict[str, Any]:
    states: Dict[str, Any] = {name: _empty_state() for name in DATASETS}
    errors: List[str] = []
    if not get_settings().MONGODB_URI:
        return {**states, "errors": errors}

    client = None
    try:
        client = get_mongo_client()
        if client is None:
            return {**states, "errors": errors}
        db = client[get_database_name()]
        for name, collection in DATASETS.items():
            state = _dataset_state(db, collection)
            if "error" in state:
                errors.append(f"{collection}: {state.pop('error')}")
            states[name] = state
        run_info = _ingestion_run_state(db)
        _apply_run_info(states, run_info)
        errors.extend(f"ingestion: {e}" for e in run_info["recent_errors"])
        states["ingestion"] = run_info
    except Exception as exc:  # noqa: BLE001 - never crash; report honestly
        errors.append(f"data health check failed: {str(exc)[:200]}")
    finally:
        if client is not None:
            client.close()
    return {**states, "errors": errors}
