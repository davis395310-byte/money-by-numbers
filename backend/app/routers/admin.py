"""Admin endpoints — Phase 8 sportsbook partner management (spec 47, 50).

Gate: the ``X-Admin-Key`` header must equal the ``ADMIN_API_KEY``
environment variable (constant-time comparison). When ``ADMIN_API_KEY`` is
not set, every admin endpoint returns 501 — there is no default key, no
demo admin, and no bypass. (Phase 11 will expand this into the full admin
panel; the gate contract stays the same.)

Endpoints:

- ``GET /api/admin/sportsbooks`` — list partners (active and inactive).
- ``POST /api/admin/sportsbooks`` — add a partner. URLs are validated
  (http/https or null); nothing is invented.
- ``PATCH /api/admin/sportsbooks/{sportsbook_id}`` — edit a partner
  (setting ``affiliate_url`` to null disables its CTAs).
- ``DELETE /api/admin/sportsbooks/{sportsbook_id}`` — deactivates the
  partner (``active=false``); click history is preserved.
- ``POST /api/admin/conversions`` — record a conversion transcribed from a
  real affiliate report. Figures must reflect real reports; the platform
  never invents revenue.
- ``GET /api/admin/conversions`` — list conversions (optional
  ``sportsbook_id`` filter).
- ``GET /api/admin/affiliate/stats`` — click/conversion/revenue aggregates
  computed from real logged data only. With no data, every section says so
  honestly; zeros are labeled "no data yet", never presented as
  performance.
- ``GET /api/admin/scheduler/jobs`` — Phase 10: operational status of
  every scheduled job (last run, next run, failures, missed flag).
- ``GET /api/admin/scheduler/runs`` — Phase 10: recent scheduler run
  records (optional ``job_name`` filter).
- ``GET /api/admin/scheduler/alerts`` — Phase 10: recent scheduler
  alerts (failures, crashes, missed runs).
"""

import hmac
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..models.business import Conversion, Sportsbook, validate_affiliate_url

router = APIRouter(prefix="/api/admin", tags=["admin"])


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


def require_admin(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Admin gate: valid key required; 501 when the key is not configured."""
    expected = get_settings().ADMIN_API_KEY
    if not expected:
        raise HTTPException(
            status_code=501,
            detail="Admin API not configured. Set ADMIN_API_KEY.",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key.")


def _require_db() -> Any:
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return db


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


class SportsbookCreate(BaseModel):
    name: str = Field(min_length=1)
    affiliate_url: Optional[str] = None
    terms: Optional[str] = None
    commission_structure: Optional[str] = None
    active: bool = True


class SportsbookUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    affiliate_url: Optional[str] = None
    terms: Optional[str] = None
    commission_structure: Optional[str] = None
    active: Optional[bool] = None


class ConversionCreate(BaseModel):
    sportsbook_id: str
    click_id: Optional[str] = None
    converted_at: datetime
    revenue_amount: Optional[float] = Field(default=None, ge=0)
    currency: str = "USD"
    notes: Optional[str] = None


@router.get("/sportsbooks", dependencies=[Depends(require_admin)])
def list_sportsbooks() -> Dict[str, Any]:
    db = _require_db()
    try:
        docs = list(db["sportsbooks"].find({}, {"_id": 0}).sort("name", 1))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    books = [_serialize(d) for d in docs]
    for b in books:
        b["cta_enabled"] = bool(b.get("active") and b.get("affiliate_url"))
    if not books:
        return {
            "sportsbooks": [],
            "note": "no sportsbook partnerships configured yet",
        }
    return {"sportsbooks": books}


@router.post("/sportsbooks", dependencies=[Depends(require_admin)])
def create_sportsbook(body: SportsbookCreate) -> Dict[str, Any]:
    db = _require_db()
    try:
        book = Sportsbook(
            name=body.name.strip(),
            affiliate_url=body.affiliate_url,
            terms=body.terms,
            commission_structure=body.commission_structure,
            active=body.active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    doc = book.model_dump(mode="json")
    # model_dump(mode="json") stringifies datetimes; store real datetimes.
    doc["created_at"] = book.created_at
    doc["updated_at"] = book.updated_at
    try:
        db["sportsbooks"].insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    out = _serialize(doc)
    out["cta_enabled"] = bool(book.active and book.affiliate_url)
    return {"status": "created", "sportsbook": out}


@router.patch("/sportsbooks/{sportsbook_id}", dependencies=[Depends(require_admin)])
def update_sportsbook(sportsbook_id: str, body: SportsbookUpdate) -> Dict[str, Any]:
    db = _require_db()
    updates: Dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.affiliate_url is not None or "affiliate_url" in body.model_fields_set:
        try:
            updates["affiliate_url"] = validate_affiliate_url(body.affiliate_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if body.terms is not None:
        updates["terms"] = body.terms
    if body.commission_structure is not None:
        updates["commission_structure"] = body.commission_structure
    if body.active is not None:
        updates["active"] = body.active
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        result = db["sportsbooks"].update_one(
            {"sportsbook_id": sportsbook_id}, {"$set": updates}
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    if result.matched_count == 0:
        raise HTTPException(
            status_code=404, detail=f"unknown sportsbook: {sportsbook_id}"
        )
    doc = db["sportsbooks"].find_one({"sportsbook_id": sportsbook_id}, {"_id": 0})
    return {"status": "updated", "sportsbook": _serialize(doc or {})}


@router.delete("/sportsbooks/{sportsbook_id}", dependencies=[Depends(require_admin)])
def deactivate_sportsbook(sportsbook_id: str) -> Dict[str, Any]:
    """Deactivate a partner (active=false). History is preserved; CTAs stop."""
    db = _require_db()
    try:
        result = db["sportsbooks"].update_one(
            {"sportsbook_id": sportsbook_id},
            {"$set": {"active": False, "updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    if result.matched_count == 0:
        raise HTTPException(
            status_code=404, detail=f"unknown sportsbook: {sportsbook_id}"
        )
    return {"status": "deactivated", "sportsbook_id": sportsbook_id}


@router.post("/conversions", dependencies=[Depends(require_admin)])
def create_conversion(body: ConversionCreate) -> Dict[str, Any]:
    """Record a conversion transcribed from a real affiliate report."""
    db = _require_db()
    try:
        partner = db["sportsbooks"].find_one(
            {"sportsbook_id": body.sportsbook_id}, {"_id": 0, "name": 1}
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    if not partner:
        raise HTTPException(
            status_code=404, detail=f"unknown sportsbook: {body.sportsbook_id}"
        )
    conv = Conversion(
        sportsbook_id=body.sportsbook_id,
        sportsbook=partner.get("name", body.sportsbook_id),
        click_id=body.click_id,
        converted_at=body.converted_at,
        revenue_amount=body.revenue_amount,
        currency=body.currency,
        notes=body.notes,
    )
    doc = conv.model_dump(mode="json")
    doc["converted_at"] = conv.converted_at
    doc["recorded_at"] = conv.recorded_at
    try:
        db["conversions"].insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    return {"status": "recorded", "conversion": _serialize(doc)}


@router.get("/conversions", dependencies=[Depends(require_admin)])
def list_conversions(
    sportsbook_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    db = _require_db()
    query: Dict[str, Any] = {}
    if sportsbook_id:
        query["sportsbook_id"] = sportsbook_id
    try:
        docs = list(
            db["conversions"]
            .find(query, {"_id": 0})
            .sort("converted_at", -1)
            .limit(500)
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    conversions = [_serialize(d) for d in docs]
    if not conversions:
        return {"conversions": [], "note": "no conversions recorded yet"}
    return {"conversions": conversions}


@router.get("/affiliate/stats", dependencies=[Depends(require_admin)])
def affiliate_stats() -> Dict[str, Any]:
    """Click/conversion/revenue aggregates from real logged data only.

    With no data, every section says so honestly. Zeros are labeled
    "no data yet" and are never presented as performance.
    """
    db = _require_db()
    try:
        click_docs = list(db["affiliate_clicks"].find({}, {"_id": 0}))
        conv_docs = list(db["conversions"].find({}, {"_id": 0}))
        partners = list(db["sportsbooks"].find({}, {"_id": 0}))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])

    clicks_total = len(click_docs)
    convs_total = len(conv_docs)
    revenue_values = [
        c["revenue_amount"]
        for c in conv_docs
        if isinstance(c.get("revenue_amount"), (int, float))
    ]

    by_book: Dict[str, Dict[str, Any]] = {}
    for p in partners:
        sid = p.get("sportsbook_id")
        if sid:
            by_book[sid] = {
                "sportsbook_id": sid,
                "name": p.get("name"),
                "active": bool(p.get("active")),
                "cta_enabled": bool(p.get("active") and p.get("affiliate_url")),
                "clicks": 0,
                "conversions": 0,
                "revenue": None,
            }
    for c in click_docs:
        for b in by_book.values():
            if b["name"] == c.get("sportsbook"):
                b["clicks"] += 1
                break
    for c in conv_docs:
        b = by_book.get(c.get("sportsbook_id"))
        if b is None:
            continue
        b["conversions"] += 1
        if isinstance(c.get("revenue_amount"), (int, float)):
            b["revenue"] = (b["revenue"] or 0) + c["revenue_amount"]

    return {
        "clicks": {
            "total": clicks_total,
            "has_data": clicks_total > 0,
            "note": None if clicks_total else "no clicks recorded yet",
        },
        "conversions": {
            "total": convs_total,
            "has_data": convs_total > 0,
            "conversion_rate": (convs_total / clicks_total) if clicks_total else None,
            "note": None if convs_total else "no conversions recorded yet",
        },
        "revenue": {
            "total": sum(revenue_values) if revenue_values else None,
            "currency": "USD",
            "reported_conversions": len(revenue_values),
            "has_data": bool(revenue_values),
            "note": None
            if revenue_values
            else "no revenue reported yet; shown as unknown, not zero",
        },
        "by_sportsbook": sorted(by_book.values(), key=lambda b: b.get("name") or ""),
    }


# ---------------------------------------------------------------------------
# Phase 11 — subscription overview (spec section 59-61)
# ---------------------------------------------------------------------------


@router.get("/subscriptions/overview", dependencies=[Depends(require_admin)])
def subscriptions_overview() -> Dict[str, Any]:
    """Aggregate subscription counts by plan and status, from the real
    ``subscriptions`` collection only. No revenue is reported here — revenue
    lives with Stripe, and this platform never invents it. Honest empty
    state when there are no subscription records."""
    db = _require_db()
    try:
        docs = list(db["subscriptions"].find({}, {"_id": 0}))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])

    by_plan: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for d in docs:
        plan = d.get("plan", "unknown")
        status = d.get("status", "unknown")
        by_plan[plan] = by_plan.get(plan, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

    active_paid = sum(
        1
        for d in docs
        if d.get("plan") in ("starter", "pro", "agency")
        and d.get("status") == "active"
    )
    return {
        "total": len(docs),
        "has_data": len(docs) > 0,
        "by_plan": by_plan,
        "by_status": by_status,
        "active_paid_subscriptions": active_paid,
        "revenue": None,
        "note": None
        if docs
        else "no subscription records yet; revenue is not estimated or reported",
    }


# ---------------------------------------------------------------------------
# Phase 10 — scheduler observability (spec section 58)
# ---------------------------------------------------------------------------

def _scheduler_runner(db: Any):
    """Build a SchedulerRunner for the admin view.

    The scheduler lives at the repo root (``pipelines/``); the API runs
    with ``backend/`` on sys.path, so the repo root is added here.
    """
    import os
    import sys

    repo_root = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from pipelines.runner import SchedulerRunner

    return SchedulerRunner(db)


@router.get("/scheduler/jobs")
def scheduler_jobs(x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    """Operational status of every scheduled job: last run, next run,
    consecutive failures, and whether the job is currently flagged as
    missed. All values are read live from the scheduler's collections."""
    require_admin(x_admin_key)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    runner = _scheduler_runner(db)
    return {"jobs": runner.all_job_statuses()}


@router.get("/scheduler/runs")
def scheduler_runs(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    job_name: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
):
    """Recent scheduler run records (append-only audit trail)."""
    require_admin(x_admin_key)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    filt: Dict[str, Any] = {}
    if job_name:
        filt["job_name"] = job_name
    docs = list(
        db["scheduler_runs"].find(filt, {"_id": 0}).sort("started_at", -1).limit(limit)
    )
    return {"runs": docs, "count": len(docs)}


@router.get("/scheduler/alerts")
def scheduler_alerts(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    job_name: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
):
    """Recent scheduler alerts: job failures, crashed runs, missed runs."""
    require_admin(x_admin_key)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    filt: Dict[str, Any] = {}
    if job_name:
        filt["job_name"] = job_name
    docs = list(
        db["scheduler_alerts"].find(filt, {"_id": 0}).sort("created_at", -1).limit(limit)
    )
    return {"alerts": docs, "count": len(docs)}


@router.post("/scheduler/jobs/{name}/run")
def scheduler_run_job(
    name: str,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    """Run one scheduled job on demand (admin only).

    Bypasses the schedule's due-ness via ``force=True`` but never bypasses
    the DB lock, the job's own safety rules, or the append-only audit
    trail. Used for operational recovery (e.g. re-firing a job whose
    container was asleep at its scheduled tick). The job runs
    synchronously in this request; expect it to take as long as the job
    itself takes (tens of seconds for weekly_picks).
    """
    require_admin(x_admin_key)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    runner = _scheduler_runner(db)
    try:
        record = runner.run_job(name, force=True)
    except Exception as exc:  # unknown job name, lock errors, etc.
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    return {
        "job_name": name,
        "run_id": getattr(record, "job_id", ""),
        "status": getattr(record.status, "value", record.status),
        "error": record.error,
        "detail": record.detail,
    }


# ---------------------------------------------------------------------------
# Data seeding (production bootstrap) — REMOVED 2026-09-17.
# ---------------------------------------------------------------------------
# POST /api/admin/seed existed only to load the initial dataset over HTTPS
# (the sandbox cannot reach Atlas on the MongoDB wire protocol). The four
# content collections were seeded and verified on 2026-09-17
# (odds 239, predictions 16, models 1, pick_explainers 16), after which the
# endpoint was deleted so production has no bulk-replace write path.
