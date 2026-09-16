"""Minimal Stripe REST client (Phase 9).

No stripe SDK dependency: the sandbox's egress proxy stalls Python HTTP
clients, so requests go through the ``curl`` binary (the same transport
the Phase 2 ingestion provider uses). In production any transport works;
this module's contract is plain HTTPS + JSON.

Only three Stripe operations are needed:
- retrieve a Price (honest price display on /api/billing/status),
- create a Checkout Session (subscribe),
- create a Customer Portal session (manage).

The secret key comes from STRIPE_SECRET_KEY only, is passed to curl via
an environment variable (never logged, never in a URL), and is never
returned to clients.
"""

import json
import subprocess
from typing import Any, Dict, Optional

from ..config import get_settings

STRIPE_API_BASE = "https://api.stripe.com/v1"
CURL_TIMEOUT_SECONDS = 25


class StripeError(Exception):
    """A Stripe API failure (network, auth, or an error object)."""


def stripe_configured() -> bool:
    return bool(get_settings().STRIPE_SECRET_KEY)


def _request(method: str, path: str, data: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    secret = get_settings().STRIPE_SECRET_KEY
    if not secret:
        raise StripeError("Stripe not configured (STRIPE_SECRET_KEY unset).")
    # NOTE: the key must not appear in argv (visible in process listings).
    # It is injected via a --config document on stdin instead.
    cmd = [
        "curl", "-sS", "--max-time", str(CURL_TIMEOUT_SECONDS),
        "--config", "-",
        "-X", method.upper(),
        STRIPE_API_BASE + path,
    ]
    config_lines = [f'user = "{secret}:"']
    if data:
        for key, value in data.items():
            # --data-urlencode for safe form encoding of values.
            config_lines.append(f"data-urlencode = \"{key}={value}\"")
    try:
        proc = subprocess.run(
            cmd,
            input="\n".join(config_lines) + "\n",
            capture_output=True,
            text=True,
            timeout=CURL_TIMEOUT_SECONDS + 10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise StripeError(f"Stripe request failed to execute: {exc}")
    if proc.returncode != 0:
        raise StripeError(
            f"Stripe request transport error: {proc.stderr.strip()[:200]}"
        )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise StripeError("Stripe returned a non-JSON response.")
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        raise StripeError(
            f"Stripe API error ({err.get('type', '?')}): "
            f"{err.get('message', 'unknown')[:200]}"
        )
    if not isinstance(payload, dict):
        raise StripeError("Unexpected Stripe response shape.")
    return payload


def retrieve_price(price_id: str) -> Dict[str, Any]:
    """Fetch a Price object: {id, unit_amount, currency, recurring interval}.

    Raises StripeError on any failure; callers degrade to DATA UNAVAILABLE.
    """
    payload = _request("GET", f"/prices/{price_id}")
    recurring = payload.get("recurring") or {}
    return {
        "id": payload.get("id"),
        "unit_amount": payload.get("unit_amount"),
        "currency": (payload.get("currency") or "usd").upper(),
        "interval": recurring.get("interval"),
        "active": bool(payload.get("active", True)),
    }


def retrieve_subscription(subscription_id: str) -> Dict[str, Any]:
    """Fetch a Subscription object (used opportunistically by webhooks)."""
    return _request("GET", f"/subscriptions/{subscription_id}")


def create_checkout_session(
    *,
    price_id: str,
    user_key: str,
    customer_email: Optional[str],
    success_url: str,
    cancel_url: str,
) -> Dict[str, Any]:
    """Create a subscription-mode Checkout Session.

    The user key travels as ``client_reference_id`` and in
    ``subscription_data[metadata][user_key]`` so verified webhook events
    can link the subscription back to the requester.
    """
    data: Dict[str, str] = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "client_reference_id": user_key,
        "subscription_data[metadata][user_key]": user_key,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if customer_email:
        data["customer_email"] = customer_email
    payload = _request("POST", "/checkout/sessions", data)
    if not payload.get("url"):
        raise StripeError("Stripe did not return a checkout URL.")
    return {"id": payload.get("id"), "url": payload.get("url")}


def create_portal_session(*, customer_id: str, return_url: str) -> Dict[str, Any]:
    payload = _request(
        "POST",
        "/billing_portal/sessions",
        {"customer": customer_id, "return_url": return_url},
    )
    if not payload.get("url"):
        raise StripeError("Stripe did not return a portal URL.")
    return {"id": payload.get("id"), "url": payload.get("url")}


def frontend_base_url() -> str:
    return (get_settings().FRONTEND_URL or "http://localhost:3000").rstrip("/")
