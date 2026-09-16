"""Postgame pipeline job (Phase 6) — spec section 40.

Runs after games complete:

- Grades every locked pick whose game is final (via
  ``backend/app/picks/settle.py``).
- Records results in the ``pick_results`` collection; grading is
  reproducible and idempotent — re-running never double-counts.
- Never touches the ``predictions`` ledger: losing picks stay on the
  public record permanently.

The job takes its database from ``context["db"]`` (a pymongo-compatible
database, e.g. mongomock in tests) or falls back to ``MONGODB_URI``.
Without a database it reports failure honestly instead of pretending.
"""

from __future__ import annotations

from typing import Any

from pipelines.scheduler import Job, JobRecord, JobStatus


class PostgameJob(Job):
    """Postgame grading job."""

    name = "postgame"

    def run(self, context: dict[str, Any] | None = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        db = context.get("db")
        if db is None:
            try:
                import os
                import sys

                backend_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"
                )
                if backend_dir not in sys.path:
                    sys.path.insert(0, backend_dir)
                from app.database import get_database_name, get_mongo_client

                client = get_mongo_client()
                db = client[get_database_name()] if client else None
            except Exception as exc:  # noqa: BLE001 - honest failure path
                record.finish(JobStatus.FAILED, error=f"no database available: {exc}")
                return record
        if db is None:
            record.finish(JobStatus.FAILED, error="no database available (MONGODB_URI not set)")
            return record

        try:
            from app.picks import run_settlement

            summary = run_settlement(
                db,
                season=context.get("season"),
                week=context.get("week"),
            )
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        if summary["error_count"]:
            record.finish(
                JobStatus.FAILED,
                error=f"{summary['error_count']} pick(s) failed to settle: "
                f"{summary['errors'][:3]}",
            )
        else:
            record.finish(JobStatus.SUCCEEDED)
        # Stash the summary on the record for operators (not part of the
        # frozen JobRecord contract, but harmless and useful).
        record.summary = summary  # type: ignore[attr-defined]
        return record
