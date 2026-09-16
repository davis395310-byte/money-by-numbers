"""Billing endpoints — Phase 9 Stripe subscriptions (spec 51-54).

- ``GET /api/billing/status`` — public: Stripe configured?, plan catalog
  (prices from Stripe or DATA UNAVAILABLE), and the requester's own plan
  read from the database — never from client input.
- ``POST /api/billing/checkout`` — signed-in users start a Stripe Checkout
  Session for a paid plan. 501 when Stripe/prices unconfigured.
- ``POST /api/billing/portal`` — signed-in subscribers open the Stripe
  Customer Portal to manage/cancel.
- ``POST /api/billing/webhook`` — Stripe event receiver. The raw body is
  signature-verified (400 on failure); verified events apply idempotently.
  This is the ONLY writer of subscription plan/status.
- ``GET /api/billing/export/picks`` — starter+: CSV of the settled pick
  ledger. Server-side plan gate.
- ``/api/billing/alerts`` — starter+: minimal bet-alert CRUD. Server-side
  plan gate.

No raw card details are ever stored or touched. Prices are never
hardcoded: they come from Stripe's API or are reported unavailable.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from ..billing import identity as ident
from ..billing.plans import PLAN_ORDER, PLANS, paid_plans, price_id_for_plan
from ..billing.stripe_client import (
    StripeError,
    create_checkout_session,
    create_portal_session,
    frontend_base_url,
    retrieve_price,
    stripe_configured,
)
from ..billing.webhooks import apply_event, parse_event, verify_stripe_signature
from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..models.business import BetAlert
from ..models.core import _validate_team_abbr

router = APIRouter(prefix="/api/billing", tags=["billing"])


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


def _price_payload(price_id: Optional[str]) -> Dict[str, Any]:
    """Honest price info: from Stripe when possible, else DATA UNAVAILABLE."""
    if not stripe_configured():
        return {
            "amount_cents": None,
            "currency": None,
            "interval": None,
            "note": "DATA UNAVAILABLE — Stripe not configured",
        }
    if not price_id:
        return {
            "amount_cents": None,
            "currency": None,
            "interval": None,
            "note": "DATA UNAVAILABLE — no price configured for this plan",
        }
    try:
        price = retrieve_price(price_id)
    except StripeError as exc:
        return {
            "amount_cents": None,
            "currency": None,
            "interval": None,
            "note": f"DATA UNAVAILABLE — could not fetch price: {str(exc)[:120]}",
        }
    return {
        "amount_cents": price.get("unit_amount"),
        "currency": price.get("currency"),
        "interval": price.get("interval"),
        "note": None,
    }


@router.get("/status")
def billing_status(request: Request) -> Dict[str, Any]:
    """Public billing status: is Stripe wired, what plans cost (from Stripe
    or honestly unavailable), and the requester's own current plan."""
    db = _db()
    user_key = ident.resolve_requester(request)
    plan = ident.get_plan(db, user_key)
    sub_doc = ident.get_subscription_doc(db, user_key) if user_key else None

    plans_out: List[Dict[str, Any]] = []
    for name in PLAN_ORDER:
        spec = PLANS[name]
        price_id = price_id_for_plan(name)
        plans_out.append(
            {
                "plan": name,
                "display_name": spec["display_name"],
                "tagline": spec["tagline"],
                "features": spec["features"],
                "limits": {
                    "numby_questions_per_day": spec["numby_questions_per_day"],
                    "csv_export": spec["csv_export"],
                    "bet_alerts": spec["bet_alerts"],
                    "seats": spec["seats"],
                },
                "price_configured": bool(price_id),
                "price": _price_payload(price_id),
            }
        )

    subscription = None
    if sub_doc:
        s = _serialize(sub_doc)
        subscription = {
            "plan": s.get("plan"),
            "status": s.get("status"),
            "current_period_end": s.get("current_period_end"),
            "cancel_at_period_end": s.get("cancel_at_period_end", False),
        }

    return {
        "status": "ok",
        "stripe_configured": stripe_configured(),
        "plans": plans_out,
        "current_user": {
            "signed_in": bool(user_key),
            "plan": plan,
            "subscription": subscription,
        },
        "note": (
            "Stripe not configured — subscriptions unavailable."
            if not stripe_configured()
            else None
        ),
    }


class CheckoutRequest(BaseModel):
    plan: str = Field(min_length=1)


@router.post("/checkout")
def create_checkout(body: CheckoutRequest, request: Request) -> Dict[str, Any]:
    """Start a Stripe Checkout Session for a paid plan.

    The user key travels as client_reference_id + subscription metadata so
    the verified webhook can link the subscription back. 501 until Stripe
    AND the plan's price are configured; 401 when anonymous.
    """
    plan = body.plan.strip().lower()
    if plan not in paid_plans():
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or non-purchasable plan: {body.plan!r}. "
            f"Purchasable plans: {', '.join(paid_plans())}.",
        )
    if not stripe_configured():
        raise HTTPException(
            status_code=501,
            detail="Subscriptions not configured (STRIPE_SECRET_KEY unset).",
        )
    price_id = price_id_for_plan(plan)
    if not price_id:
        raise HTTPException(
            status_code=501,
            detail=f"No Stripe price configured for the {plan} plan.",
        )
    db = _require_db()
    user_key = ident.require_signed_in(request)

    base = frontend_base_url()
    try:
        session = create_checkout_session(
            price_id=price_id,
            user_key=user_key,
            customer_email=user_key if "@" in user_key else None,
            success_url=f"{base}/pricing?checkout=success",
            cancel_url=f"{base}/pricing?checkout=cancelled",
        )
    except StripeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:200])
    return {"status": "ok", "checkout_url": session["url"], "session_id": session["id"]}


@router.post("/portal")
def customer_portal(request: Request) -> Dict[str, Any]:
    """Open the Stripe Customer Portal for the requester's subscription."""
    if not stripe_configured():
        raise HTTPException(
            status_code=501,
            detail="Subscriptions not configured (STRIPE_SECRET_KEY unset).",
        )
    db = _require_db()
    user_key = ident.require_signed_in(request)
    sub_doc = ident.get_subscription_doc(db, user_key)
    customer_id = (sub_doc or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=404,
            detail="No Stripe subscription found for this account.",
        )
    try:
        portal = create_portal_session(
            customer_id=customer_id, return_url=f"{frontend_base_url()}/pricing"
        )
    except StripeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:200])
    return {"status": "ok", "portal_url": portal["url"]}


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Dict[str, Any]:
    """Stripe event receiver (spec 54).

    Verifies the Stripe-Signature header against STRIPE_WEBHOOK_SECRET;
    rejects anything unsigned (400) and applies verified events idempotently.
    """
    secret = get_settings().STRIPE_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(
            status_code=501,
            detail="Webhook not configured (STRIPE_WEBHOOK_SECRET unset).",
        )
    raw = await request.body()
    signature = request.headers.get("Stripe-Signature")
    if not verify_stripe_signature(raw, signature, secret):
        raise HTTPException(
            status_code=400, detail="Invalid or missing Stripe-Signature."
        )
    try:
        event = parse_event(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db = _require_db()
    return apply_event(db, event)


# ---------------------------------------------------------------------------
# Premium feature: CSV export of the settled pick ledger (starter+).
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "prediction_id",
    "game_id",
    "season",
    "week",
    "home_team",
    "away_team",
    "model_version",
    "market",
    "side",
    "confidence",
    "model_probability",
    "predicted_winner",
    "market_line_at_pick",
    "market_price_at_pick",
    "edge",
    "result",
    "actual_home_score",
    "actual_away_score",
    "actual_winner",
    "brier_contribution",
    "settled_at",
]


@router.get("/export/picks")
def export_picks_csv(request: Request) -> Response:
    """CSV of settled picks. Starter plan or higher (server-side gate)."""
    db = _require_db()
    ident.require_plan(request, db, "starter")
    try:
        rows = list(db["pick_results"].find({}, {"_id": 0}).sort("settled_at", 1))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = dict(row)
        for key, value in list(clean.items()):
            if hasattr(value, "isoformat"):
                clean[key] = value.isoformat()
        writer.writerow(clean)
    filename = f"money-by-numbers-picks-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Premium feature: bet alerts (starter+).
# ---------------------------------------------------------------------------


class AlertCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    team: Optional[str] = None
    game_id: Optional[str] = None
    edge_threshold: Optional[float] = Field(default=None, ge=0, le=1)


@router.get("/alerts")
def list_alerts(request: Request) -> Dict[str, Any]:
    db = _require_db()
    user_key = ident.require_signed_in(request)
    ident.require_plan(request, db, "starter")
    try:
        docs = list(
            db["alerts"].find({"user_id": user_key}, {"_id": 0}).sort("created_at", -1)
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    alerts = [_serialize(d) for d in docs]
    if not alerts:
        return {"alerts": [], "note": "no alerts yet"}
    return {"alerts": alerts}


@router.post("/alerts")
def create_alert(body: AlertCreate, request: Request) -> Dict[str, Any]:
    db = _require_db()
    user_key = ident.require_signed_in(request)
    ident.require_plan(request, db, "starter")
    team = body.team.strip().upper() if body.team else None
    if team:
        try:
            _validate_team_abbr(team)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    alert = BetAlert(
        user_id=user_key,
        name=body.name.strip(),
        team=team,
        game_id=body.game_id.strip() if body.game_id else None,
        edge_threshold=body.edge_threshold,
    )
    doc = alert.model_dump(mode="json")
    doc["created_at"] = alert.created_at
    try:
        db["alerts"].insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    return {"status": "created", "alert": _serialize(doc)}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: str, request: Request) -> Dict[str, Any]:
    db = _require_db()
    user_key = ident.require_signed_in(request)
    ident.require_plan(request, db, "starter")
    try:
        result = db["alerts"].delete_one(
            {"alert_id": alert_id, "user_id": user_key}
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)[:200])
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="unknown alert")
    return {"status": "deleted", "alert_id": alert_id}
