"""Stripe webhook verification and event application (Phase 9, spec 54).

Security contract:
- Every webhook is verified with the Stripe-Signature header (HMAC-SHA256
  of ``<timestamp>.<raw_body>`` against STRIPE_WEBHOOK_SECRET, with a
  5-minute timestamp tolerance). Missing/invalid signatures -> 400.
  Unsigned webhooks are rejected, never processed.
- Events are idempotent: each Stripe event id is recorded in
  ``stripe_events``; redeliveries are acknowledged without re-applying
  side effects.
- Subscription rows are written ONLY from verified events. Client input
  can never set a plan or status.

Handled event types:
- ``checkout.session.completed`` — links the Stripe customer/subscription
  to our user key (best effort: the subscription.* events below carry the
  authoritative state).
- ``customer.subscription.created`` / ``customer.subscription.updated`` —
  upsert the subscription row: plan from the price id, status from Stripe.
- ``customer.subscription.deleted`` — status -> canceled.
- ``invoice.payment_failed`` — status -> past_due.
- Anything else: recorded as processed with a note; never a 500.
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .plans import plan_for_price_id
from .stripe_client import StripeError, retrieve_subscription

SIGNATURE_TOLERANCE_SECONDS = 300

# Stripe subscription status -> our status. Unknown values are preserved
# verbatim (never silently mapped to "active").
STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "canceled": "canceled",
    "unpaid": "unpaid",
    "incomplete": "incomplete",
    "incomplete_expired": "canceled",
    "paused": "canceled",
}


def verify_stripe_signature(
    payload: bytes, signature_header: Optional[str], secret: str
) -> bool:
    """Verify the Stripe-Signature header for a raw webhook body.

    Header format: ``t=<unix_ts>,v1=<hex_hmac>[,v1=<hex_hmac>...]``.
    The expected signature is HMAC-SHA256(secret, ``b"<t>.<payload>"``).
    Returns False for any missing/malformed/expired/mismatched input.
    """
    if not payload or not signature_header or not secret:
        return False
    timestamp: Optional[int] = None
    signatures: list = []
    for part in signature_header.split(","):
        part = part.strip()
        if part.startswith("t="):
            try:
                timestamp = int(part[2:])
            except ValueError:
                return False
        elif part.startswith("v1="):
            signatures.append(part[3:])
    if timestamp is None or not signatures:
        return False
    if abs(time.time() - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def parse_event(payload: bytes) -> Dict[str, Any]:
    """Parse and minimally validate a webhook JSON body."""
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("webhook body is not valid JSON")
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise ValueError("webhook body is not a Stripe event (missing id/type)")
    return event


def _event_object(event: Dict[str, Any]) -> Dict[str, Any]:
    obj = (event.get("data") or {}).get("object") or {}
    return obj if isinstance(obj, dict) else {}


def _price_id_from_subscription(sub: Dict[str, Any]) -> Optional[str]:
    try:
        items = (sub.get("items") or {}).get("data") or []
        if items and isinstance(items[0], dict):
            price = items[0].get("price") or {}
            return price.get("id")
    except Exception:
        pass
    return None


def _subscription_doc(
    *,
    user_id: Optional[str],
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str],
    plan: Optional[str],
    status: str,
    current_period_end: Optional[int],
    cancel_at_period_end: bool = False,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc: Dict[str, Any] = {
        "user_id": user_id,
        "plan": plan or "free",
        "status": status,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "cancel_at_period_end": bool(cancel_at_period_end),
        "updated_at": now,
    }
    if current_period_end:
        doc["current_period_end"] = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        )
    return doc


def apply_event(db: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a verified Stripe event idempotently.

    Returns a summary dict; always safe to call with a verified event.
    Never raises for unknown event types — those are recorded and noted.
    """
    event_id = event["id"]
    event_type = event["type"]

    existing = db["stripe_events"].find_one({"event_id": event_id})
    if existing and existing.get("processed"):
        return {"status": "already_processed", "event_id": event_id}

    now = datetime.now(timezone.utc)
    db["stripe_events"].update_one(
        {"event_id": event_id},
        {
            "$setOnInsert": {
                "event_id": event_id,
                "event_type": event_type,
                "received_at": now,
            }
        },
        upsert=True,
    )

    try:
        note = _apply(db, event)
    except Exception as exc:  # noqa: BLE001 - record, don't 500 webhooks
        note = f"handler note: {str(exc)[:200]}"

    db["stripe_events"].update_one(
        {"event_id": event_id},
        {"$set": {"processed": True, "note": note}},
    )
    return {"status": "processed", "event_id": event_id, "note": note}


def _apply(db: Any, event: Dict[str, Any]) -> str:
    event_type = event["type"]
    obj = _event_object(event)

    if event_type == "checkout.session.completed":
        return _apply_checkout_completed(db, obj)
    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        return _apply_subscription_upsert(db, obj)
    if event_type == "customer.subscription.deleted":
        return _apply_subscription_deleted(db, obj)
    if event_type == "invoice.payment_failed":
        return _apply_payment_failed(db, obj)
    return f"unhandled event type recorded without side effects: {event_type}"


def _user_key_from(obj: Dict[str, Any]) -> Optional[str]:
    metadata = obj.get("metadata") or {}
    key = metadata.get("user_key") or obj.get("client_reference_id")
    return str(key) if key else None


def _apply_checkout_completed(db: Any, session: Dict[str, Any]) -> str:
    """Link a completed checkout to our user.

    The subscription.created/updated events carry authoritative plan state;
    here we opportunistically resolve it via the API so a subscription row
    exists even if event ordering is odd. API failure is a note, not an
    error — the subscription events will complete the picture.
    """
    user_key = _user_key_from(session)
    customer_id = session.get("customer")
    sub_id = session.get("subscription")
    if not user_key:
        return "checkout completed but no user_key metadata; recorded only"
    if not sub_id:
        return "checkout completed with no subscription id; recorded only"

    plan: Optional[str] = None
    status = "incomplete"
    period_end: Optional[int] = None
    try:
        sub = retrieve_subscription(sub_id)
        plan = plan_for_price_id(_price_id_from_subscription(sub))
        status = STATUS_MAP.get(sub.get("status"), sub.get("status") or "incomplete")
        period_end = sub.get("current_period_end")
    except StripeError as exc:
        return f"checkout linked; subscription resolve deferred ({str(exc)[:120]})"

    db["subscriptions"].update_one(
        {"stripe_subscription_id": sub_id},
        {
            "$set": _subscription_doc(
                user_id=user_key,
                stripe_customer_id=customer_id,
                stripe_subscription_id=sub_id,
                plan=plan,
                status=status,
                current_period_end=period_end,
            ),
            "$setOnInsert": {
                "subscription_id": event_subscription_uid(),
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )
    return f"checkout linked: user subscription row upserted (plan={plan or 'free'})"


def _apply_subscription_upsert(db: Any, sub: Dict[str, Any]) -> str:
    sub_id = sub.get("id")
    if not sub_id:
        return "subscription event without id; recorded only"
    user_key = _user_key_from(sub)
    if not user_key:
        # Fall back to the user on the existing row for this Stripe sub.
        row = db["subscriptions"].find_one({"stripe_subscription_id": sub_id})
        user_key = (row or {}).get("user_id")
    if not user_key:
        return "subscription event without linkable user; recorded only"

    plan = plan_for_price_id(_price_id_from_subscription(sub))
    raw_status = sub.get("status") or "incomplete"
    status = STATUS_MAP.get(raw_status, raw_status)
    db["subscriptions"].update_one(
        {"stripe_subscription_id": sub_id},
        {
            "$set": _subscription_doc(
                user_id=user_key,
                stripe_customer_id=sub.get("customer"),
                stripe_subscription_id=sub_id,
                plan=plan,
                status=status,
                current_period_end=sub.get("current_period_end"),
                cancel_at_period_end=sub.get("cancel_at_period_end", False),
            ),
            "$setOnInsert": {
                "subscription_id": event_subscription_uid(),
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )
    return f"subscription upserted: plan={plan or 'free'} status={status}"


def _apply_subscription_deleted(db: Any, sub: Dict[str, Any]) -> str:
    sub_id = sub.get("id")
    if not sub_id:
        return "subscription.deleted without id; recorded only"
    result = db["subscriptions"].update_one(
        {"stripe_subscription_id": sub_id},
        {
            "$set": {
                "status": "canceled",
                "plan": "free",
                "cancel_at_period_end": False,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if result.matched_count == 0:
        return "subscription.deleted for unknown subscription; recorded only"
    return "subscription marked canceled; plan reverted to free"


def _apply_payment_failed(db: Any, invoice: Dict[str, Any]) -> str:
    sub_id = invoice.get("subscription")
    if not sub_id:
        return "invoice.payment_failed without subscription; recorded only"
    result = db["subscriptions"].update_one(
        {"stripe_subscription_id": sub_id},
        {"$set": {"status": "past_due", "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        return "invoice.payment_failed for unknown subscription; recorded only"
    return "subscription marked past_due"


def event_subscription_uid() -> str:
    """Unique id for subscription rows created from webhook events."""
    return uuid.uuid4().hex
