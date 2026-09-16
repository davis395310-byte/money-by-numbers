"""GET /api/health — real, honest service health checks.

Every check reports its actual state; nothing is faked:
- application: ok when the app is serving
- database: ok on a real ping, not_configured when MONGODB_URI is unset,
  error with detail when the ping fails
- ml / data / injuries / weather: not_implemented (phase 1 has no ML or
  ingestion pipelines yet)
- odds / stripe: ok only when the corresponding API key is configured
  (never calls the external API from a health check)
- scheduler: not_implemented

Overall status is "degraded" when the database check is in error, else "ok".
"""

from typing import Any, Dict

from fastapi import APIRouter
from pymongo import MongoClient

from ..config import get_settings
from ..database import get_database_name

router = APIRouter(tags=["health"])


def _check_database() -> Dict[str, Any]:
    settings = get_settings()
    if not settings.MONGODB_URI:
        return {"status": "not_configured", "detail": "MONGODB_URI is not set"}
    client = None
    try:
        client = MongoClient(
            settings.MONGODB_URI, serverSelectionTimeoutMS=1500, connectTimeoutMS=1500
        )
        client.admin.command("ping")
        return {
            "status": "ok",
            "detail": f"ping succeeded; database={get_database_name()}",
        }
    except Exception as exc:  # noqa: BLE001 - surfaced honestly as the detail
        return {"status": "error", "detail": f"ping failed: {str(exc)[:200]}"}
    finally:
        if client is not None:
            client.close()


@router.get("/api/health")
def health() -> Dict[str, Any]:
    settings = get_settings()
    checks: Dict[str, Any] = {
        "application": {"status": "ok"},
        "database": _check_database(),
        "ml": {"status": "not_implemented"},
        "data": {"status": "not_implemented"},
        "injuries": {"status": "not_implemented"},
        "weather": {"status": "not_implemented"},
        "odds": {
            "status": "ok" if settings.ODDS_API_KEY else "not_configured",
        },
        "stripe": {
            "status": "ok" if settings.STRIPE_SECRET_KEY else "not_configured",
        },
        "scheduler": {"status": "not_implemented"},
    }
    status = "degraded" if checks["database"]["status"] == "error" else "ok"
    return {"status": status, "version": settings.APP_VERSION, "checks": checks}
