"""Identity resolution and plan gating (Phase 9, spec 52-53).

Identity comes from the signed ``mbn_session`` cookie (Phase 1 Google
OAuth): the requester key is the Google ``sub`` when present, else the
verified email. Nothing is trusted from request bodies — subscription
status is read from the ``subscriptions`` collection, which only the
Stripe webhook handler writes.

Free is the default for everyone: anonymous traffic, users with no
subscription, and subscriptions whose status is not active/trialing.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from ..auth_sessions import SESSION_COOKIE_NAME, verify_session_value
from .plans import PLANS, plan_meets

# Only these Stripe statuses count as "paying". past_due / canceled /
# incomplete / unpaid fall back to the free tier (honest: no silent cutoff
# of the core product, and no premium access without a good subscription).
ACTIVE_STATUSES = ("active", "trialing")


def resolve_requester(request: Request) -> Optional[str]:
    """Return the signed-in requester's key, or None when anonymous.

    The cookie is HMAC-verified; a missing, malformed, expired, or
    mis-signed cookie yields None — never an exception to the caller.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        data = verify_session_value(token)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    key = data.get("sub") or data.get("email")
    return str(key) if key else None


def get_plan(db: Any | None, user_key: Optional[str]) -> str:
    """Resolve the effective plan for a requester. Free unless a live
    subscription says otherwise. Never raises on missing data."""
    if db is None or not user_key:
        return "free"
    try:
        doc = db["subscriptions"].find_one(
            {"user_id": user_key, "status": {"$in": list(ACTIVE_STATUSES)}},
            sort=[("current_period_end", -1)],
        )
    except Exception:
        return "free"
    plan = (doc or {}).get("plan")
    return plan if plan in PLANS else "free"


def get_subscription_doc(db: Any | None, user_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """The requester's latest subscription document, or None."""
    if db is None or not user_key:
        return None
    try:
        return db["subscriptions"].find_one(
            {"user_id": user_key}, sort=[("updated_at", -1)]
        )
    except Exception:
        return None


def require_signed_in(request: Request) -> str:
    """Return the requester key, or 401 when anonymous."""
    key = resolve_requester(request)
    if not key:
        raise HTTPException(
            status_code=401,
            detail="Sign in to use this feature.",
        )
    return key


def require_plan(request: Request, db: Any | None, minimum: str) -> str:
    """Return the requester's plan, enforcing a minimum tier (402 when the
    plan is too low). Free-tier users keep the full core product; only
    premium features gate here."""
    user_key = require_signed_in(request)
    plan = get_plan(db, user_key)
    if not plan_meets(plan, minimum):
        raise HTTPException(
            status_code=402,
            detail=(
                f"This feature requires the {PLANS[minimum]['display_name']} "
                f"plan or higher. Your current plan: "
                f"{PLANS[plan]['display_name']}."
            ),
        )
    return plan


# ---------------------------------------------------------------------------
# Numby daily question quotas (server-side, enforced in the chat endpoint).
# ---------------------------------------------------------------------------


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_numby_quota(db: Any | None, identity_key: str, plan: str) -> None:
    """Raise 402 when the identity's daily Numby question limit is reached.

    No database -> no counting -> no enforcement (the stateless honest
    fallback used by every other no-DB path in this codebase).
    """
    if db is None:
        return
    limit = PLANS.get(plan, PLANS["free"])["numby_questions_per_day"]
    try:
        doc = db["usage_counters"].find_one(
            {"key": identity_key, "day": _today_utc()}
        )
    except Exception:
        return  # a counter read failure must not break chat
    used = int((doc or {}).get("numby_questions", 0) or 0)
    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Daily Numby question limit reached ({limit} per day on the "
                f"{PLANS.get(plan, PLANS['free'])['display_name']} plan). "
                "The limit resets tomorrow."
            ),
        )


def record_numby_usage(db: Any | None, identity_key: str) -> None:
    """Increment today's Numby question counter. Failures are swallowed —
    usage tracking must never break chat."""
    if db is None:
        return
    try:
        db["usage_counters"].update_one(
            {"key": identity_key, "day": _today_utc()},
            {
                "$inc": {"numby_questions": 1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception:
        pass
