"""Production scheduler runner for MONEY BY NUMBERS (Phase 10, spec 55-58).

Two deployment modes:

1. **External cron** (recommended): ``python pipelines/run_scheduler.py --once``
   every 5-15 minutes from cron/systemd. Each invocation runs whatever jobs
   are due and exits. Crash-safe by construction: state lives in MongoDB.
2. **Daemon loop**: ``python pipelines/run_scheduler.py --loop`` — ticks
   internally every ``--interval`` seconds.

Restart safety (the core guarantee):

- The ``scheduler_jobs`` collection holds one lock document per job. Lock
  acquisition is a single atomic ``find_one_and_update`` with an upsert:
  two runners can never hold the same job's lock, so a job **cannot
  double-run after a restart** — the restarted process sees the fresh lock
  and stands down.
- If a runner dies mid-run, its lock goes stale (no heartbeat past
  ``stale_minutes``). The next runner to attempt the job reclaims the
  lock, marks the orphaned ``scheduler_runs`` record ``"crashed"``, and
  emits an alert — the gap is visible, never silently ignored.
- Every attempt writes an append-only ``scheduler_runs`` record
  (``run_id`` unique): start/end timestamps, status, duration, detail,
  error. Skips are recorded too, with the reason.
- Missed-run detection (``check_missed_runs``) compares each job's last
  *successful* run against its ``max_gap_hours`` and alerts once per gap
  until a success clears it. Out-of-season jobs are exempt.

Jobs must never fabricate data: when a provider key is missing, the
budget is exhausted, or the database is unreachable, the job raises
``SkipJob`` (recorded as "skipped") or returns FAILED with an honest
error. Nothing is invented to fill the gap.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from pipelines.alerts import emit_alert
from pipelines.schedule import (
    JOB_SCHEDULES,
    in_nfl_season,
    is_due,
    last_tick,
    load_job_class,
    next_tick,
    utcnow,
)
from pipelines.scheduler import JobRecord, JobStatus, SkipJob

log = logging.getLogger("mbn.scheduler.runner")

RUNS_COLLECTION = "scheduler_runs"
JOBS_COLLECTION = "scheduler_jobs"
ALERTS_COLLECTION = "scheduler_alerts"


def _event(event: str, **fields: Any) -> None:
    """One structured JSON log line per scheduler event."""
    payload = {"event": event}
    payload.update(fields)
    log.info(json.dumps(payload, default=str))


def _utc_naive(dt: datetime) -> datetime:
    """Normalize to naive UTC for MongoDB.

    pymongo decodes stored datetimes as naive UTC, so every datetime
    written to or compared against the database must be naive UTC —
    otherwise comparisons raise TypeError. Callers may pass aware
    datetimes; they are converted.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _find_schedule(name: str) -> Dict[str, Any]:
    """Look up a job in this runner's roster (module binding, so tests
    can substitute a fake schedule)."""
    for entry in JOB_SCHEDULES:
        if entry["name"] == name:
            return entry
    raise KeyError(f"unknown scheduled job: {name!r}")


def ensure_scheduler_indexes(db: Any) -> None:
    """Idempotent index creation for the scheduler's collections."""
    db[RUNS_COLLECTION].create_index([("run_id", ASCENDING)],
                                    unique=True, name="scheduler_runs_run_id_unique")
    db[RUNS_COLLECTION].create_index([("job_name", ASCENDING), ("started_at", DESCENDING)],
                                    name="scheduler_runs_job_started")
    db[ALERTS_COLLECTION].create_index([("alert_id", ASCENDING)],
                                      unique=True, name="scheduler_alerts_id_unique")
    db[ALERTS_COLLECTION].create_index([("job_name", ASCENDING), ("created_at", DESCENDING)],
                                      name="scheduler_alerts_job_created")
    # scheduler_jobs indexes already exist in backend/app/database.py
    # INDEX_SPECS (name unique); ensure them here too for standalone use.
    db[JOBS_COLLECTION].create_index([("name", ASCENDING)],
                                     unique=True, name="scheduler_jobs_name_unique")


class SchedulerRunner:
    """Runs scheduled jobs with DB-backed locking and full audit trail."""

    def __init__(self, db: Any, stale_minutes: Optional[int] = None,
                 locked_by: Optional[str] = None) -> None:
        if db is None:
            raise ValueError("SchedulerRunner requires a database (no in-memory mode: "
                             "locks and run history must survive restarts).")
        self.db = db
        self.stale_minutes = stale_minutes if stale_minutes is not None else int(
            os.environ.get("SCHEDULER_STALE_MINUTES", "120"))
        self.locked_by = locked_by or f"{socket.gethostname()}:{os.getpid()}"
        ensure_scheduler_indexes(db)

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------
    def _acquire_lock(self, name: str, run_id: str, now: datetime) -> Optional[Dict[str, Any]]:
        """Atomically take the job lock. Returns the *previous* lock doc
        (None on first ever acquisition), or a sentinel when the lock is
        held by a live runner elsewhere.

        Returns:
            "BUSY" (str) when another runner holds a fresh lock;
            the previous document (dict/None) when we acquired it.
        """
        cutoff = now - timedelta(minutes=self.stale_minutes)
        filt: Dict[str, Any] = {
            "name": name,
            "$or": [
                {"state": {"$ne": "running"}},
                {"locked_at": {"$lt": cutoff}},
                {"locked_at": None},
            ],
        }
        update = {
            "$set": {
                "job_id": name,
                "name": name,
                "state": "running",
                "run_id": run_id,
                "locked_at": now,
                "locked_by": self.locked_by,
                "updated_at": now,
            },
            "$setOnInsert": {
                "last_run_at": None,
                "last_status": None,
                "last_success_at": None,
                "last_error": None,
                "consecutive_failures": 0,
                "detail": {},
                "missed_alerted_at": None,
            },
        }
        before = None
        try:
            before = self.db[JOBS_COLLECTION].find_one_and_update(
                filt, update, upsert=True, return_document=ReturnDocument.BEFORE
            )
        except DuplicateKeyError:
            # Lost the insert race: another runner created/took the lock.
            return "BUSY"  # type: ignore[return-value]
        if before is None:
            # Either a fresh insert (we hold the lock) or the filter did
            # not match (busy). Distinguish by reading the current doc.
            current = self.db[JOBS_COLLECTION].find_one({"name": name})
            if current and current.get("run_id") == run_id:
                return None  # fresh insert: lock is ours
            return "BUSY"  # type: ignore[return-value]
        state = before.get("state")
        locked_at = before.get("locked_at")
        if state == "running" and locked_at is not None and locked_at >= cutoff:
            return "BUSY"  # type: ignore[return-value]
        # We hold the lock; `before` describes the previous holder.
        return before

    def _release_lock(self, name: str, run_id: str, now: datetime) -> None:
        self.db[JOBS_COLLECTION].update_one(
            {"name": name, "run_id": run_id, "state": "running"},
            {"$set": {"state": "idle", "locked_at": None, "updated_at": now}},
        )

    def _mark_crashed(self, job_name: str, stale_doc: Dict[str, Any], now: datetime) -> None:
        """A previous runner died holding the lock: record the crash."""
        stale_run_id = stale_doc.get("run_id")
        if stale_run_id:
            self.db[RUNS_COLLECTION].update_one(
                {"run_id": stale_run_id, "status": "running"},
                {"$set": {"status": JobStatus.CRASHED.value, "finished_at": now,
                          "error": "runner died or hung: lock went stale and was reclaimed"}},
            )
        emit_alert(
            self.db, job_name, "job_crashed",
            f"Previous run {str(stale_run_id)[:8]} died holding the lock "
            f"(holder: {stale_doc.get('locked_by')}); lock reclaimed.",
            run_id=stale_run_id,
        )
        _event("lock_reclaimed", job=job_name, stale_run_id=stale_run_id)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def run_job(self, name: str, context: Optional[Dict[str, Any]] = None,
                now: Optional[datetime] = None, force: bool = False) -> JobRecord:
        """Run one job by name (locking + audit). ``force`` bypasses
        due-ness but never bypasses the lock."""
        now = _utc_naive(now or utcnow())
        entry = _find_schedule(name)
        run_id = uuid.uuid4().hex
        record = JobRecord(job_id=f"{name}-{run_id[:8]}")

        prev = self._acquire_lock(name, run_id, now)
        if prev == "BUSY":
            record.finish(JobStatus.SKIPPED,
                          error="lock held by another live runner; not double-running")
            _event("job_busy", job=name, run_id=run_id)
            return record
        if isinstance(prev, dict) and prev.get("state") == "running":
            # Stale lock: the previous runner is dead.
            self._mark_crashed(name, prev, now)

        run_doc = {
            "run_id": run_id,
            "job_name": name,
            "started_at": now,
            "finished_at": None,
            "status": JobStatus.RUNNING.value,
            "duration_seconds": None,
            "detail": {},
            "error": None,
        }
        self.db[RUNS_COLLECTION].insert_one(dict(run_doc))
        _event("job_start", job=name, run_id=run_id, force=force)

        ctx = dict(context or {})
        ctx.setdefault("db", self.db)
        ctx.setdefault("now", now)
        ctx.setdefault("run_id", run_id)

        try:
            job = load_job_class(entry["job_path"])()
            job_record = job.run(ctx)
            status = job_record.status
            error = job_record.error
            detail = dict(job_record.detail or {})
        except SkipJob as exc:
            status, error, detail = JobStatus.SKIPPED, str(exc), {}
        except Exception as exc:  # noqa: BLE001 - jobs must not crash the runner
            status, error, detail = JobStatus.FAILED, str(exc)[:1000], {}

        finished = _utc_naive(utcnow())
        duration = (finished - now).total_seconds()
        self.db[RUNS_COLLECTION].update_one(
            {"run_id": run_id},
            {"$set": {"finished_at": finished, "status": status.value,
                      "duration_seconds": duration, "detail": detail,
                      "error": error}},
        )

        set_fields: Dict[str, Any] = {
            "last_run_at": finished,
            "last_status": status.value,
            "last_error": error,
            "detail": detail,
            "updated_at": finished,
        }
        update_doc: Dict[str, Any] = {"$set": set_fields}
        if status == JobStatus.SUCCEEDED:
            set_fields["last_success_at"] = finished
            set_fields["consecutive_failures"] = 0
            set_fields["missed_alerted_at"] = None
        elif status == JobStatus.FAILED:
            update_doc["$inc"] = {"consecutive_failures": 1}
        self.db[JOBS_COLLECTION].update_one({"name": name}, update_doc)
        if status == JobStatus.FAILED:
            failures = (self.db[JOBS_COLLECTION].find_one({"name": name}) or {}).get(
                "consecutive_failures", 0)
            emit_alert(self.db, name, "job_failed",
                       f"Run {run_id[:8]} failed (consecutive failures: {failures}): "
                       f"{(error or '')[:400]}",
                       run_id=run_id)
        self._release_lock(name, run_id, finished)

        record.status = status
        record.error = error
        record.detail = detail
        record.end_time = finished
        record.duration_seconds = duration
        _event("job_end", job=name, run_id=run_id, status=status.value,
               duration_s=round(duration, 2), error=(error or "")[:200])
        return record

    def run_due_jobs(self, now: Optional[datetime] = None,
                     context: Optional[Dict[str, Any]] = None) -> List[JobRecord]:
        """Run every scheduled job whose tick has arrived."""
        now = _utc_naive(now or utcnow())
        records: List[JobRecord] = []
        for entry in JOB_SCHEDULES:
            name = entry["name"]
            if entry.get("in_season_only") and not in_nfl_season(now):
                records.append(self._record_skip(
                    name, "out of season — in-season job skipped", now, context))
                continue
            job_doc = self.db[JOBS_COLLECTION].find_one({"name": name})
            last_run_at = job_doc.get("last_run_at") if job_doc else None
            if not is_due(entry["cadence"], now, last_run_at):
                continue
            records.append(self.run_job(name, context=context, now=now))
        self.check_missed_runs(now=now)
        return records

    def _record_skip(self, name: str, reason: str, now: datetime,
                     context: Optional[Dict[str, Any]]) -> JobRecord:
        """Record an out-of-season skip without taking the lock."""
        run_id = uuid.uuid4().hex
        record = JobRecord(job_id=f"{name}-{run_id[:8]}")
        record.finish(JobStatus.SKIPPED, error=reason)
        record.detail = {"reason": reason}
        self.db[RUNS_COLLECTION].insert_one({
            "run_id": run_id,
            "job_name": name,
            "started_at": now,
            "finished_at": record.end_time,
            "status": JobStatus.SKIPPED.value,
            "duration_seconds": record.duration_seconds,
            "detail": {"reason": reason},
            "error": reason,
        })
        self.db[JOBS_COLLECTION].update_one(
            {"name": name},
            {"$set": {"job_id": name, "name": name, "last_run_at": record.end_time,
                      "last_status": JobStatus.SKIPPED.value, "last_error": reason,
                      "updated_at": now},
             "$setOnInsert": {"state": "idle", "consecutive_failures": 0,
                              "last_success_at": None, "detail": {},
                              "missed_alerted_at": None}},
            upsert=True,
        )
        _event("job_skipped", job=name, run_id=run_id, reason=reason)
        return record

    # ------------------------------------------------------------------
    # Missed-run detection
    # ------------------------------------------------------------------
    def check_missed_runs(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Flag jobs with no successful run inside their max_gap window.

        Returns the newly emitted alert documents. Alerts fire once per
        gap (tracked via ``missed_alerted_at``) until a success clears it.
        """
        now = _utc_naive(now or utcnow())
        new_alerts: List[Dict[str, Any]] = []
        for entry in JOB_SCHEDULES:
            name = entry["name"]
            if entry.get("in_season_only") and not in_nfl_season(now):
                continue
            doc = self.db[JOBS_COLLECTION].find_one({"name": name}) or {}
            if doc.get("missed_alerted_at"):
                continue  # already alerted for the current gap
            gap = timedelta(hours=entry["max_gap_hours"])
            last_success = doc.get("last_success_at")
            if last_success is not None:
                missed = (now - last_success) > gap
                detail = f"no successful run in {(now - last_success).days} day(s)"
            else:
                # Never succeeded: missed only if the job has been on the
                # schedule longer than the gap (avoids alerting on a fresh
                # deploy before the first tick).
                tick = last_tick(entry["cadence"], now)
                missed = tick < (now - gap)
                detail = "never completed successfully since scheduling began"
            if missed:
                alert = emit_alert(
                    self.db, name, "run_missed",
                    f"No successful run within {entry['max_gap_hours']}h window: {detail}.")
                new_alerts.append(alert)
                self.db[JOBS_COLLECTION].update_one(
                    {"name": name},
                    {"$set": {"missed_alerted_at": now, "updated_at": now}},
                    upsert=True,
                )
                _event("run_missed", job=name)
        return new_alerts

    # ------------------------------------------------------------------
    # Status (admin view backing)
    # ------------------------------------------------------------------
    def job_status(self, name: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        """One job's operational status for the admin view."""
        now = _utc_naive(now or utcnow())
        entry = _find_schedule(name)
        doc = self.db[JOBS_COLLECTION].find_one({"name": name}) or {}
        last_success = doc.get("last_success_at")
        gap = timedelta(hours=entry["max_gap_hours"])
        if entry.get("in_season_only") and not in_nfl_season(now):
            missed = False
        elif last_success is not None:
            missed = (now - last_success) > gap
        else:
            missed = last_tick(entry["cadence"], now) < (now - gap)
        recent = list(self.db[RUNS_COLLECTION].find(
            {"job_name": name}, {"_id": 0}).sort("started_at", DESCENDING).limit(5))
        return {
            "name": name,
            "description": entry["description"],
            "cadence": entry["cadence"],
            "in_season_only": entry["in_season_only"],
            "in_season_now": in_nfl_season(now),
            "state": doc.get("state", "idle"),
            "last_run_at": doc.get("last_run_at"),
            "last_status": doc.get("last_status"),
            "last_success_at": last_success,
            "last_error": doc.get("last_error"),
            "consecutive_failures": doc.get("consecutive_failures", 0),
            "next_run_at": next_tick(entry["cadence"], now),
            "missed": missed,
            "recent_runs": recent,
        }

    def all_job_statuses(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        return [self.job_status(e["name"], now=now) for e in JOB_SCHEDULES]
