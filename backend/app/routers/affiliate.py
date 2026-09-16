"""Affiliate referrals — public endpoints (Phase 8, spec sections 46-49).

- ``GET /api/affiliate/disclosure`` — the affiliate disclosure text
  (env-configurable via ``AFFILIATE_DISCLOSURE_TEXT``; honest default).
- ``GET /api/affiliate/cta-status`` — server-side CTA gate: sportsbook CTA
  buttons may render ONLY when this reports ``ctas_enabled: true``.
- ``GET /r/{sportsbook_id}`` — tracked referral: logs the click to
  ``affiliate_clicks`` (reusing the Phase 1 ``build_click_record`` helper),
  then 302-redirects to the sportsbook's configured affiliate URL.

Hard rules (spec 46):

- The ``sportsbooks`` collection starts EMPTY. No partnerships are
  invented, and no affiliate URL is ever hardcoded or guessed.
- The redirect fires ONLY for an ACTIVE sportsbook with a configured
  ``affiliate_url``. Unknown id, inactive partner, or missing URL -> 404
  with an honest reason. The destination is the configured URL verbatim.
- Click context (game/edge/model_version) is recorded when supplied; the
  click is anonymous unless the caller passes a session id.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..models.business import build_click_record

router = APIRouter(tags=["affiliate"])

DEFAULT_DISCLOSURE = (
    "Money By Numbers may receive compensation when users access "
    "sportsbook offers through links on this site."
)

NO_PARTNERS_NOTE = (
    "No sportsbook partnerships are configured yet. Sportsbook CTAs stay "
    "disabled until real affiliate URLs exist."
)


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


def _active_partners(db: Any) -> List[Dict[str, Any]]:
    """Active sportsbooks with a configured affiliate URL (CTA-eligible)."""
    try:
        docs = list(
            db["sportsbooks"].find(
                {"active": True, "affiliate_url": {"$ne": None}}, {"_id": 0}
            )
        )
    except Exception:
        return []
    return [d for d in docs if d.get("affiliate_url")]


@router.get("/api/affiliate/disclosure")
def affiliate_disclosure() -> Dict[str, Any]:
    """Affiliate disclosure text (env-configurable, honest default)."""
    configured = get_settings().AFFILIATE_DISCLOSURE_TEXT
    text = configured.strip() if configured and configured.strip() else DEFAULT_DISCLOSURE
    return {"disclosure": text, "configured": bool(configured and configured.strip())}


@router.get("/api/affiliate/cta-status")
def cta_status() -> Dict[str, Any]:
    """Server-side CTA gate.

    Frontends must consult this before rendering any sportsbook CTA:
    ``ctas_enabled`` is true only when at least one ACTIVE sportsbook has a
    configured affiliate URL. With zero partners configured, CTAs stay
    disabled — no "coming soon" buttons implying partnerships exist.
    """
    db = _db()
    if db is None:
        return {
            "ctas_enabled": False,
            "sportsbooks": [],
            "reason": "database not configured",
        }
    partners = _active_partners(db)
    if not partners:
        return {
            "ctas_enabled": False,
            "sportsbooks": [],
            "reason": "no sportsbook partnerships configured",
            "note": NO_PARTNERS_NOTE,
        }
    return {
        "ctas_enabled": True,
        "sportsbooks": [
            {"sportsbook_id": p.get("sportsbook_id"), "name": p.get("name")}
            for p in partners
            if p.get("sportsbook_id") and p.get("name")
        ],
    }


@router.get("/r/{sportsbook_id}")
def tracked_referral(
    sportsbook_id: str,
    cta_location: str = Query(default="redirect"),
    game_id: Optional[str] = Query(default=None),
    market: Optional[str] = Query(default=None),
    side: Optional[str] = Query(default=None),
    edge: Optional[float] = Query(default=None),
    model_version: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
) -> Any:
    """Log an affiliate click and 302-redirect to the configured URL.

    404 (never a redirect) when the sportsbook id is unknown, the partner
    is inactive, or no affiliate URL is configured. The destination is the
    stored URL verbatim — never invented or guessed.
    """
    db = _db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="database not configured; referral cannot be tracked",
        )
    try:
        partner = db["sportsbooks"].find_one({"sportsbook_id": sportsbook_id}, {"_id": 0})
    except Exception:
        partner = None
    if not partner:
        raise HTTPException(
            status_code=404, detail=f"unknown sportsbook: {sportsbook_id}"
        )
    if not partner.get("active", False):
        raise HTTPException(
            status_code=404,
            detail=f"sportsbook '{partner.get('name')}' is not active",
        )
    destination = partner.get("affiliate_url")
    if not destination:
        raise HTTPException(
            status_code=404,
            detail=(
                f"sportsbook '{partner.get('name')}' has no affiliate URL "
                "configured; referral refused rather than guessed"
            ),
        )

    record = build_click_record(
        sportsbook=partner.get("name"),
        cta_location=cta_location,
        timestamp=datetime.now(timezone.utc),
        destination=destination,
        session_id=session_id,
        game_id=game_id,
        edge=edge,
        model_version=model_version,
        line=f"{market} {side}".strip() if market or side else None,
    )
    try:
        db["affiliate_clicks"].insert_one(record)
    except Exception:
        raise HTTPException(
            status_code=503, detail="failed to record click; referral aborted"
        )

    return RedirectResponse(url=destination, status_code=302)
