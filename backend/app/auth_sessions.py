"""Signed session cookie helpers.

Sessions are HMAC-SHA256 signed tokens: base64url(JSON payload) + "." + hex
signature. Requires SESSION_SECRET to be set; raises RuntimeError otherwise.
There are no demo users and no bypass tokens.
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

SESSION_COOKIE_NAME = "mbn_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _get_secret() -> str:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is not set; session cookies cannot be signed or verified."
        )
    return secret


def create_session_value(data: Dict[str, Any], max_age_seconds: int = SESSION_MAX_AGE_SECONDS) -> str:
    """Create a signed session token for the given payload data."""
    payload = {
        "data": data,
        "exp": (datetime.now(timezone.utc) + timedelta(seconds=max_age_seconds)).timestamp(),
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    sig = hmac.new(_get_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session_value(token: str) -> Dict[str, Any]:
    """Verify a session token and return its payload data.

    Raises RuntimeError if SESSION_SECRET is missing, ValueError if the
    signature is invalid or the token has expired.
    """
    raw, sep, sig = token.partition(".")
    if not sep or not raw or not sig:
        raise ValueError("Malformed session token")
    expected = hmac.new(_get_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid session signature")
    payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
        raise ValueError("Session expired")
    return payload["data"]
