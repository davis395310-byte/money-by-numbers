"""Shared helpers for pipeline jobs (Phase 10).

Every job follows the same contract:

- ``context["db"]`` may carry a pymongo-compatible database (mongomock in
  tests); otherwise the job falls back to ``MONGODB_URI``.
- ``context["now"]`` may carry the current time (tests fix it).
- Jobs never fabricate data: missing config/keys/budget raise ``SkipJob``
  (recorded as "skipped"); unexpected errors return FAILED with the honest
  message.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def repo_root() -> str:
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )


def ensure_backend() -> str:
    """Put ``backend/`` on sys.path so ``app.*`` is importable."""
    backend_dir = os.path.join(repo_root(), "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    return backend_dir


def ensure_repo_root() -> str:
    """Put the repo root on sys.path so ``ml.*`` is importable."""
    root = repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_db(context: Optional[Dict[str, Any]]) -> Any:
    """Resolve the database from context or MONGODB_URI.

    Raises RuntimeError (recorded as an honest FAILED run) when no
    database is available. The scheduler never runs jobs against a
    throwaway in-memory database: locks and run history must survive
    restarts.
    """
    context = context or {}
    db = context.get("db")
    if db is not None:
        return db
    ensure_backend()
    from app.database import get_database_name, get_mongo_client

    try:
        client = get_mongo_client()
    except Exception as exc:  # noqa: BLE001 - honest failure path
        raise RuntimeError(f"could not connect to MongoDB: {exc}") from exc
    if client is None:
        raise RuntimeError("no database available (MONGODB_URI not set)")
    return client[get_database_name()]
