"""Daily pipeline job (Phase 10) — spec section 38.

Runs once per day:

- Ingests the latest game schedule and results from nflverse for the
  current season (free, no API key). The ingestion layer is idempotent:
  re-running the same day changes nothing.
- Records a light health check (games stored for the season) in the run
  detail.

Without a database the job fails honestly. If nflverse is unreachable,
the provider error is recorded — the job never invents games to fill
the gap.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pipelines.jobs._common import require_db, utcnow
from pipelines.schedule import current_season
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob


def _default_ingest(db: Any, season: int) -> Dict[str, Any]:
    """Real ingestion: nflverse -> MongoDB via the Phase 2 pipeline."""
    from pipelines.jobs._common import ensure_backend

    ensure_backend()
    from app.data_providers import NflverseProvider
    from app.ingestion import run_ingestion

    return run_ingestion(db, NflverseProvider(), [season])


class DailyJob(Job):
    """Daily NFL data ingestion + health check."""

    name = "daily_ingest"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        now = context.get("now") or utcnow()
        season = current_season(now)
        ingest_fn = context.get("ingest_fn") or _default_ingest

        try:
            summary = ingest_fn(db, season)
        except SkipJob:
            raise
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED,
                          error=f"ingestion failed: {exc}"[:500])
            return record

        try:
            games_stored = db["games"].count_documents({"season": season})
        except Exception:  # noqa: BLE001 - health check is best effort
            games_stored = None

        status = (summary or {}).get("status")
        record.detail = {
            "season": season,
            "ingestion_status": status,
            "records_received": (summary or {}).get("records_received"),
            "records_accepted": (summary or {}).get("records_accepted"),
            "records_rejected": (summary or {}).get("records_rejected"),
            "games_stored_for_season": games_stored,
        }
        if status in ("success", "partial"):
            record.finish(JobStatus.SUCCEEDED)
        else:
            errors = (summary or {}).get("errors") or []
            record.finish(
                JobStatus.FAILED,
                error=f"ingestion status={status}: {str(errors[:2])[:400]}",
            )
        return record
