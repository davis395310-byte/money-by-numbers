"""Numby chat endpoints (Phase 7, spec sections 41-45).

- ``POST /api/numby/chat`` — chat with Numby. Body: ``{"message": str,
  "session_id": str | null, "user_id": str | null}``. Answers are grounded
  ONLY in the platform's real data; when data is missing Numby says so.
- ``GET /api/numby/history`` — recent messages for a session.
- ``GET /api/numby/status`` — honest AI-provider status (configured vs
  basic mode).

Conversation history persists in the ``conversations`` collection when a
database is configured; without one, chat still works statelessly and
says so honestly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..billing import identity as billing_identity
from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..numby import memory
from ..numby.providers import provider_status
from ..numby.responder import respond

router = APIRouter(tags=["numby"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=memory.MAX_MESSAGE_CHARS)
    session_id: Optional[str] = None
    user_id: Optional[str] = None


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


@router.get("/api/numby/status")
def numby_status() -> Dict[str, Any]:
    """Honest AI-provider status: llm mode vs basic (template) mode."""
    status = provider_status()
    return {"status": "ok", **status}


@router.post("/api/numby/chat")
def chat(body: ChatRequest, http_request: Request) -> Dict[str, Any]:
    """Chat with Numby. Grounded answers only — never fabricated."""
    message = body.message.strip()
    if not message:
        return {
            "status": "error",
            "reason": "message must not be empty",
            "reply": None,
        }
    db = _db()
    session_id = (body.session_id or "").strip() or None
    history: List[Dict[str, str]] = []
    persistent = db is not None

    if persistent:
        try:
            session = memory.get_or_create_session(db, session_id, body.user_id)
            session_id = session["session_id"]
            history = memory.get_history(db, session_id)
            memory.append_message(db, session_id, "user", message)
        except memory.ConversationError as exc:
            return {"status": "error", "reason": str(exc), "reply": None}
        except Exception as exc:  # noqa: BLE001 - honest failure, not a crash
            return {"status": "error", "reason": str(exc)[:200], "reply": None}

    # Phase 9 plan gating: daily Numby question quotas, enforced
    # server-side. No database -> no counting -> no enforcement (the
    # stateless honest fallback).
    user_key = billing_identity.resolve_requester(http_request)
    plan = billing_identity.get_plan(db, user_key)
    identity_key = f"user:{user_key}" if user_key else f"anon:{session_id or 'unknown'}"
    try:
        billing_identity.check_numby_quota(db, identity_key, plan)
    except HTTPException as exc:
        return {
            "status": "quota_exceeded",
            "reason": exc.detail,
            "reply": None,
            "plan": plan,
            "upgrade_url": "/pricing",
            "session_id": session_id,
            "persistent": persistent,
        }

    result = respond(db, message, history)

    if persistent:
        try:
            memory.append_message(db, session_id, "assistant", result["reply"])  # type: ignore[arg-type]
        except Exception:
            pass  # history write failure must not eat the answer
        billing_identity.record_numby_usage(db, identity_key)

    return {
        "status": "ok",
        "reply": result["reply"],
        "sources": result["sources"],
        "ai_mode": result["ai_mode"],
        "disclaimer": result["disclaimer"],
        "session_id": session_id,
        "persistent": persistent,
    }


@router.get("/api/numby/history")
def history(session_id: str = Query(min_length=1)) -> Dict[str, Any]:
    """Recent messages for a session (capped)."""
    db = _db()
    if db is None:
        return {
            "status": "no_history",
            "reason": "database not configured",
            "messages": [],
        }
    try:
        messages = memory.get_history(db, session_id.strip())
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "messages": []}
    return {
        "status": "ok",
        "session_id": session_id.strip(),
        "messages": [
            {"role": m["role"], "content": m["content"]} for m in messages
        ],
    }
