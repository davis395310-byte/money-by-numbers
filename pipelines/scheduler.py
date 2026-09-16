"""Scheduler primitives for MONEY BY NUMBERS background jobs.

Defines the job record dataclass and the abstract Job base class that all
pipeline jobs (daily, weekly, postgame, retrain) must implement.

No scheduler is wired up in Phase 1: this is the interface contract only.
The production wiring (Celery/background workers per the architecture doc)
is described in docs/deployment.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    """Lifecycle states of a job run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    """The job intentionally did not run (missing config, out of season,
    budget exhausted). A skip is an honest non-run, never a silent gap:
    it is recorded like any other run."""
    CRASHED = "crashed"
    """A previous run held the lock past the staleness horizon — the
    process died or hung. Recorded by the runner that reclaims the lock
    so the gap is visible, never silently ignored."""


class SkipJob(Exception):
    """Raise inside Job.run to record an honest skip instead of a failure.

    Used when a job cannot run for a legitimate, non-error reason: a
    provider key is not configured, the credit budget is exhausted, the
    job is out of season, or there is no new data to process. The run is
    recorded with status "skipped" and a reason — never fabricated data
    to fill the gap.
    """


@dataclass
class JobRecord:
    """A single recorded run of a job."""

    job_id: str
    """Unique identifier for this job run."""

    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When the run started (UTC)."""

    end_time: datetime | None = None
    """When the run finished (UTC); None while running."""

    status: JobStatus = JobStatus.PENDING
    """Current lifecycle status."""

    duration_seconds: float | None = None
    """Wall-clock duration once finished; None while running."""

    error: str | None = None
    """Error message if the run failed; None otherwise."""

    retry_count: int = 0
    """Number of retries attempted for this run."""

    detail: dict = field(default_factory=dict)
    """Operator-facing summary of what the run did (counts, seasons,
    versions). Persisted on the scheduler_runs record."""

    def finish(self, status: JobStatus, error: str | None = None) -> None:
        """Mark the run finished, stamping end time and duration."""
        self.end_time = datetime.now(timezone.utc)
        self.duration_seconds = (self.end_time - self.start_time).total_seconds()
        self.status = status
        self.error = error


class Job(ABC):
    """Abstract base class for all pipeline jobs.

    Subclasses implement :meth:`run` with the job's actual work and expose a
    stable :attr:`name` used for scheduling and logging.
    """

    name: str = "base"

    @abstractmethod
    def run(self, context: dict[str, Any] | None = None) -> JobRecord:
        """Execute the job and return its completed JobRecord.

        Implementations should catch their own errors, record them on the
        JobRecord, and apply the retry policy rather than crashing the
        scheduler process.
        """
        raise NotImplementedError("Job.run is not implemented.")
