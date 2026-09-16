"""MONEY BY NUMBERS — pipelines package.

Background job layer: scheduled data ingestion, prediction, postgame grading,
and model retraining jobs (Phase 10 automation, spec sections 55-58).

Modules:
    scheduler   JobRecord dataclass, JobStatus, SkipJob, abstract Job base.
    schedule    Job roster, cadences, season helpers, due-ness (pure).
    runner      SchedulerRunner: DB-backed locking, run records, missed-run
                detection, alerting, admin status (restart-safe).
    alerts      Alert persistence + webhook forwarding.
    run_scheduler  CLI entry point (--once / --loop / --job / --status).
    jobs.daily  DailyJob: nflverse ingestion + health check.
    jobs.odds_pull  OddsPullJob: Tue/Fri/Sun odds pulls, budget-gated.
    jobs.weekly WeeklyJob: injury + weather refresh.
    jobs.postgame PostgameJob: idempotent settlement.
    jobs.retrain RetrainJob: monthly challenger-only retrain (never promotes).
"""

from pipelines.alerts import emit_alert
from pipelines.runner import SchedulerRunner
from pipelines.schedule import (
    JOB_SCHEDULES,
    current_season,
    get_schedule,
    in_nfl_season,
    is_due,
    last_tick,
    next_tick,
)
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

__all__ = [
    "Job",
    "JobRecord",
    "JobStatus",
    "SkipJob",
    "SchedulerRunner",
    "emit_alert",
    "JOB_SCHEDULES",
    "current_season",
    "get_schedule",
    "in_nfl_season",
    "is_due",
    "last_tick",
    "next_tick",
]
