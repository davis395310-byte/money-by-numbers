"""Odds pull job (Phase 10) — spec section 56.

Runs Tue/Fri/Sun during the NFL season (in-season gating is enforced by
the scheduler roster). Pulls a fresh odds snapshot from The Odds API and
stores it for the edge engine's line-movement history.

Credit-budget discipline (Phase 4):

- No ``ODDS_API_KEY`` -> honest skip (no pull attempted, no credits
  spent).
- ``check_budget`` runs BEFORE any network call: an exhausted or unknown
  monthly balance -> honest skip, never a pull.
- Credit usage is recorded after every pull so the ledger stays accurate.

An audit document is written to ``ingestion_runs`` (source="odds") with
received/accepted/rejected counts, mirroring the manual script.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pipelines.jobs._common import require_db, utcnow
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_pull(db: Any) -> Dict[str, Any]:
    """Real pull: budget check -> The Odds API -> validated snapshots."""
    from pipelines.jobs._common import ensure_backend

    ensure_backend()
    from app.data_providers import TheOddsApiProvider, check_budget
    from app.models.core import OddsSnapshot

    started_at = _utcnow()
    run_id = uuid.uuid4().hex

    ok, reason = check_budget(db, min_required=1)
    if not ok:
        raise SkipJob(f"odds pull skipped — credit budget: {reason}")

    provider = TheOddsApiProvider()
    result = provider.fetch_odds(db=db)
    provider.record_credit_usage(db)

    received = len(result.records)
    accepted = 0
    rejected = 0
    errors = list(result.errors)
    now = _utcnow()
    for record in result.records:
        try:
            snap = OddsSnapshot(**record)
        except Exception as exc:  # noqa: BLE001 - invalid record rejected, never stored
            rejected += 1
            errors.append(
                f"snapshot rejected ({record.get('sportsbook')}/"
                f"{record.get('game_id')}): {exc}"
            )
            continue
        doc = snap.model_dump()
        doc["ingested_at"] = now
        db["odds"].insert_one(doc)
        accepted += 1

    finished_at = _utcnow()
    status = "success" if not errors else ("partial" if accepted else "failed")
    db["ingestion_runs"].insert_one({
        "run_id": run_id,
        "source": "odds",
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "records_received": received,
        "records_accepted": accepted,
        "records_rejected": rejected,
        "errors": errors[:20],
    })
    return {
        "run_id": run_id,
        "status": status,
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors[:5],
        "credits_remaining": provider.last_credit_remaining,
        "credits_used": provider.last_credit_used,
    }


class OddsPullJob(Job):
    """Scheduled odds snapshot pull with credit-budget protection."""

    name = "odds_pull"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        from pipelines.jobs._common import ensure_backend

        ensure_backend()
        from app.config import get_settings

        settings = get_settings()
        if not (settings.ODDS_API_KEY and settings.ODDS_API_KEY.strip()):
            raise SkipJob(
                "ODDS_API_KEY not configured — odds pull skipped "
                "(no pull attempted, no credits spent)"
            )

        pull_fn = context.get("pull_fn") or _default_pull
        try:
            summary = pull_fn(db)
        except SkipJob:
            raise
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED,
                          error=f"odds pull failed: {exc}"[:500])
            return record

        record.detail = {
            "ingestion_status": summary.get("status"),
            "snapshots_received": summary.get("received"),
            "snapshots_accepted": summary.get("accepted"),
            "snapshots_rejected": summary.get("rejected"),
            "credits_remaining": summary.get("credits_remaining"),
            "credits_used": summary.get("credits_used"),
        }
        if summary.get("status") in ("success", "partial"):
            record.finish(JobStatus.SUCCEEDED)
        else:
            record.finish(
                JobStatus.FAILED,
                error=f"odds pull status={summary.get('status')}: "
                      f"{str(summary.get('errors') or [])[:400]}",
            )
        return record
