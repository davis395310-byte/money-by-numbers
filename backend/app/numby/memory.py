"""Conversation memory for Numby (Phase 7, spec 52 ``conversations``).

Stores per-session chat history so Numby can reference earlier turns.
Sensible limits, enforced here (not trusted to callers):

- ``MAX_MESSAGE_CHARS`` (1000): overlong user messages are rejected.
- ``MAX_HISTORY_MESSAGES`` (20): only the most recent turns are returned
  as context for the next answer.
- ``MAX_SESSION_MESSAGES`` (60): a session stops accepting new messages
  past this point (the user starts a new chat).

Each stored message: ``role`` ("user"/"assistant"), ``content``,
``created_at``. Sessions are keyed by ``session_id`` (UUID hex generated
server-side when the client doesn't supply one) and optionally tagged
with ``user_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20
MAX_SESSION_MESSAGES = 60


class ConversationError(ValueError):
    """Raised for overlong messages or exhausted sessions."""


def new_session_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_session(
    db: Any, session_id: Optional[str], user_id: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch a session doc, creating it when absent. Returns the doc."""
    sid = (session_id or "").strip() or new_session_id()
    doc = db["conversations"].find_one({"session_id": sid}, {"_id": 0})
    if doc is None:
        doc = {
            "session_id": sid,
            "user_id": user_id,
            "messages": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        db["conversations"].insert_one(doc)
        doc = db["conversations"].find_one({"session_id": sid}, {"_id": 0})
    return doc  # type: ignore[return-value]


def append_message(
    db: Any, session_id: str, role: str, content: str
) -> Dict[str, Any]:
    """Append one message; enforces length and session caps."""
    if role not in ("user", "assistant"):
        raise ConversationError("role must be 'user' or 'assistant'")
    if len(content) > MAX_MESSAGE_CHARS:
        raise ConversationError(
            f"Message too long ({len(content)} chars; max {MAX_MESSAGE_CHARS})."
        )
    doc = db["conversations"].find_one({"session_id": session_id}, {"_id": 0})
    if doc is None:
        raise ConversationError("Unknown session.")
    messages = doc.get("messages") or []
    if len(messages) >= MAX_SESSION_MESSAGES:
        raise ConversationError(
            "This chat has reached its message limit — please start a new chat."
        )
    entry = {"role": role, "content": content, "created_at": _now()}
    db["conversations"].update_one(
        {"session_id": session_id},
        {"$push": {"messages": entry}, "$set": {"updated_at": _now()}},
    )
    return entry


def get_history(db: Any, session_id: str) -> List[Dict[str, Any]]:
    """Most recent turns for prompt context (capped)."""
    doc = db["conversations"].find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        return []
    messages = doc.get("messages") or []
    tail = messages[-MAX_HISTORY_MESSAGES:]
    return [
        {"role": m.get("role"), "content": m.get("content")}
        for m in tail
        if m.get("role") in ("user", "assistant")
    ]


def session_message_count(db: Any, session_id: str) -> int:
    doc = db["conversations"].find_one(
        {"session_id": session_id}, {"_id": 0, "messages": 1}
    )
    return len(doc.get("messages") or []) if doc else 0
