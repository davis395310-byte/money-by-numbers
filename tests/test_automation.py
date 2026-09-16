"""Phase 10 automation tests: scheduler restart-safety, missed-run detection,
idempotent reruns, graceful skips, challenger-only retraining, and the
admin observability endpoints."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipelines.runner as runner_module
from pipelines.runner import SchedulerRunner
from pipelines.schedule import (
    current_season,
    in_nfl_season,
    is_due,
    last_tick,
    next_tick,
)
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)  # a Wednesday, in season


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeJob(Job):
    name = "fake_job"
    calls = 0

    def run(self, context=None):
        FakeJob.calls += 1
        record = JobRecord(job_id="fake")
        record.detail = {"ran": True}
        record.finish(JobStatus.SUCCEEDED)
        return record


class FakeFailingJob(Job):
    name = "fake_job"

    def run(self, context=None):
        record = JobRecord(job_id="fake")
        record.finish(JobStatus.FAILED, error="boom")
        return record


FAKE_SCHEDULE = [
    {
        "name": "fake_job",
        "job_path": "fake:FakeJob",
        "cadence": {"kind": "daily", "hour_utc": 0, "minute_utc": 0},
        "in_season_only": False,
        "max_gap_hours": 40,
        "description": "test job",
    }
]

FAKE_SEASONAL_SCHEDULE = [
    {
        "name": "fake_seasonal",
        "job_path": "fake:FakeJob",
        "cadence": {"kind": "daily", "hour_utc": 0, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 40,
        "description": "in-season test job",
    }
]


@pytest.fixture()
def db():
    return mongomock.MongoClient()["money_by_numbers"]


@pytest.fixture()
def runner(db):
    return SchedulerRunner(db, stale_minutes=60, locked_by="test-runner")


@pytest.fixture()
def fake_schedule(monkeypatch):
    monkeypatch.setattr(runner_module, "JOB_SCHEDULES", FAKE_SCHEDULE)
    monkeypatch.setattr(runner_module, "load_job_class", lambda path: FakeJob)
    FakeJob.calls = 0
    return FAKE_SCHEDULE


# ---------------------------------------------------------------------------
# Pure scheduling logic
# ---------------------------------------------------------------------------

def test_current_season():
    assert current_season(datetime(2026, 9, 9, tzinfo=timezone.utc)) == 2026
    assert current_season(datetime(2027, 1, 15, tzinfo=timezone.utc)) == 2026
    assert current_season(datetime(2026, 5, 1, tzinfo=timezone.utc)) == 2025
    assert current_season(datetime(2026, 12, 31, tzinfo=timezone.utc)) == 2026


def test_in_nfl_season():
    assert in_nfl_season(datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert in_nfl_season(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert in_nfl_season(datetime(2027, 2, 10, tzinfo=timezone.utc))
    assert not in_nfl_season(datetime(2027, 2, 16, tzinfo=timezone.utc))
    assert not in_nfl_season(datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert not in_nfl_season(datetime(2026, 4, 1, tzinfo=timezone.utc))


def test_last_tick_daily():
    cadence = {"kind": "daily", "hour_utc": 10, "minute_utc": 0}
    assert last_tick(cadence, NOW) == datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    early = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
    assert last_tick(cadence, early) == datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)


def test_last_tick_weekly_days():
    # Tue/Fri/Sun 12:00; NOW is Wed 2026-09-09 12:00 -> last tick Tue 9/8 12:00
    cadence = {"kind": "weekly_days", "days": [1, 4, 6], "hour_utc": 12, "minute_utc": 0}
    assert last_tick(cadence, NOW) == datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def test_last_tick_monthly():
    cadence = {"kind": "monthly", "day": 1, "hour_utc": 7, "minute_utc": 0}
    assert last_tick(cadence, NOW) == datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)


def test_next_tick():
    daily = {"kind": "daily", "hour_utc": 10, "minute_utc": 0}
    assert next_tick(daily, NOW) == datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    weekly = {"kind": "weekly_days", "days": [1, 4, 6], "hour_utc": 12, "minute_utc": 0}
    assert next_tick(weekly, NOW) == datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)  # Fri
    monthly = {"kind": "monthly", "day": 1, "hour_utc": 7, "minute_utc": 0}
    assert next_tick(monthly, NOW) == datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)


def test_next_tick_monthly_clamps_short_months():
    cadence = {"kind": "monthly", "day": 31, "hour_utc": 7, "minute_utc": 0}
    now = datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc)
    assert next_tick(cadence, now) == datetime(2027, 2, 28, 7, 0, tzinfo=timezone.utc)


def test_is_due():
    cadence = {"kind": "daily", "hour_utc": 10, "minute_utc": 0}
    assert is_due(cadence, NOW, None)
    assert is_due(cadence, NOW, datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc))
    assert not is_due(cadence, NOW, datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc))
    assert not is_due(cadence, NOW, datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# Restart safety: locks, crashes, no double-run
# ---------------------------------------------------------------------------

def test_lock_prevents_double_run(runner, fake_schedule):
    now = NOW
    db = runner.db
    db["scheduler_jobs"].insert_one({
        "job_id": "fake_job", "name": "fake_job", "state": "running",
        "run_id": "other-runner-run", "locked_at": now, "locked_by": "other:1",
    })
    record = runner.run_job("fake_job", now=now)
    assert record.status == JobStatus.SKIPPED
    assert "lock held" in (record.error or "")
    assert FakeJob.calls == 0
    # No run record for the job itself beyond the busy-skip: the busy path
    # returns before inserting a running record.
    assert db["scheduler_runs"].count_documents({"job_name": "fake_job"}) == 0


def test_stale_lock_reclaimed_and_crash_recorded(runner, fake_schedule):
    db = runner.db
    stale_at = NOW - timedelta(hours=3)
    db["scheduler_jobs"].insert_one({
        "job_id": "fake_job", "name": "fake_job", "state": "running",
        "run_id": "dead-run", "locked_at": stale_at, "locked_by": "dead:1",
    })
    db["scheduler_runs"].insert_one({
        "run_id": "dead-run", "job_name": "fake_job", "started_at": stale_at,
        "status": "running",
    })
    record = runner.run_job("fake_job", now=NOW)
    assert record.status == JobStatus.SUCCEEDED
    assert FakeJob.calls == 1
    crashed = db["scheduler_runs"].find_one({"run_id": "dead-run"})
    assert crashed["status"] == "crashed"
    alert = db["scheduler_alerts"].find_one({"kind": "job_crashed"})
    assert alert is not None and alert["job_name"] == "fake_job"
    # Lock released after the run
    assert db["scheduler_jobs"].find_one({"name": "fake_job"})["state"] == "idle"


def test_sequential_reruns_get_distinct_run_records(runner, fake_schedule):
    r1 = runner.run_job("fake_job", now=NOW)
    r2 = runner.run_job("fake_job", now=NOW + timedelta(minutes=1))
    assert r1.status == r2.status == JobStatus.SUCCEEDED
    assert r1.job_id != r2.job_id
    docs = list(runner.db["scheduler_runs"].find({"job_name": "fake_job"}))
    assert len(docs) == 2
    assert len({d["run_id"] for d in docs}) == 2
    assert FakeJob.calls == 2


def test_run_due_jobs_respects_due_ness(runner, fake_schedule):
    # First tick: due (never ran).
    records = runner.run_due_jobs(now=NOW)
    assert len(records) == 1 and records[0].status == JobStatus.SUCCEEDED
    # Same tick again: not due, nothing runs.
    records = runner.run_due_jobs(now=NOW + timedelta(minutes=5))
    assert records == []
    assert FakeJob.calls == 1


def test_out_of_season_job_skipped_with_record(runner, monkeypatch):
    monkeypatch.setattr(runner_module, "JOB_SCHEDULES", FAKE_SEASONAL_SCHEDULE)
    monkeypatch.setattr(runner_module, "load_job_class", lambda path: FakeJob)
    FakeJob.calls = 0
    july = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    records = runner.run_due_jobs(now=july)
    assert len(records) == 1
    assert records[0].status == JobStatus.SKIPPED
    assert "out of season" in (records[0].error or "")
    assert FakeJob.calls == 0
    doc = runner.db["scheduler_runs"].find_one({"job_name": "fake_seasonal"})
    assert doc is not None and doc["status"] == "skipped"


# ---------------------------------------------------------------------------
# Missed-run detection
# ---------------------------------------------------------------------------

def test_missed_run_alerts_once_until_success(runner, fake_schedule):
    db = runner.db
    db["scheduler_jobs"].insert_one({
        "job_id": "fake_job", "name": "fake_job", "state": "idle",
        "last_success_at": NOW - timedelta(hours=100),  # max_gap is 40h
    })
    alerts = runner.check_missed_runs(now=NOW)
    assert len(alerts) == 1 and alerts[0]["kind"] == "run_missed"
    # Second check: no duplicate alert for the same gap.
    assert runner.check_missed_runs(now=NOW + timedelta(hours=1)) == []
    # A success clears the flag.
    runner.run_job("fake_job", now=NOW + timedelta(hours=2))
    doc = db["scheduler_jobs"].find_one({"name": "fake_job"})
    assert doc["missed_alerted_at"] is None


def test_no_missed_alert_when_recent_success(runner, fake_schedule):
    runner.db["scheduler_jobs"].insert_one({
        "job_id": "fake_job", "name": "fake_job", "state": "idle",
        "last_success_at": NOW - timedelta(hours=5),
    })
    assert runner.check_missed_runs(now=NOW) == []


def test_failed_run_emits_alert_and_counts(runner, fake_schedule, monkeypatch):
    monkeypatch.setattr(runner_module, "load_job_class", lambda path: FakeFailingJob)
    record = runner.run_job("fake_job", now=NOW)
    assert record.status == JobStatus.FAILED
    db = runner.db
    alert = db["scheduler_alerts"].find_one({"kind": "job_failed"})
    assert alert is not None and "boom" in alert["message"]
    assert db["scheduler_jobs"].find_one({"name": "fake_job"})["consecutive_failures"] == 1
    # A later success resets the counter.
    monkeypatch.setattr(runner_module, "load_job_class", lambda path: FakeJob)
    FakeJob.calls = 0
    runner.run_job("fake_job", now=NOW + timedelta(minutes=1))
    assert db["scheduler_jobs"].find_one({"name": "fake_job"})["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Job-level behavior
# ---------------------------------------------------------------------------

def test_daily_job_ingests_current_season(db):
    from pipelines.jobs.daily import DailyJob

    seen = {}

    def fake_ingest(database, season):
        seen["season"] = season
        return {"status": "success", "records_received": 10,
                "records_accepted": 10, "records_rejected": 0, "errors": []}

    record = DailyJob().run({"db": db, "now": NOW, "ingest_fn": fake_ingest})
    assert record.status == JobStatus.SUCCEEDED
    assert seen["season"] == 2026
    assert record.detail["season"] == 2026


def test_daily_job_fails_honestly_without_db():
    from pipelines.jobs.daily import DailyJob

    record = DailyJob().run({"db": None, "now": NOW})
    assert record.status == JobStatus.FAILED
    assert "no database" in (record.error or "").lower()


def test_daily_job_records_provider_failure(db):
    from pipelines.jobs.daily import DailyJob

    def bad_ingest(database, season):
        raise RuntimeError("nflverse unreachable")

    record = DailyJob().run({"db": db, "now": NOW, "ingest_fn": bad_ingest})
    assert record.status == JobStatus.FAILED
    assert "nflverse unreachable" in (record.error or "")


def test_odds_job_skips_without_key(db):
    from pipelines.jobs.odds_pull import OddsPullJob

    with pytest.raises(SkipJob, match="ODDS_API_KEY not configured"):
        OddsPullJob().run({"db": db, "now": NOW})


def test_odds_job_pulls_with_key(db, monkeypatch):
    from pipelines.jobs.odds_pull import OddsPullJob

    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    summary = {"status": "success", "received": 5, "accepted": 5,
               "rejected": 0, "errors": [], "credits_remaining": 495,
               "credits_used": 1}
    record = OddsPullJob().run({"db": db, "now": NOW,
                                "pull_fn": lambda database: summary})
    assert record.status == JobStatus.SUCCEEDED
    assert record.detail["credits_remaining"] == 495


def test_weekly_job_refreshes_context(db):
    from pipelines.jobs.weekly import WeeklyJob

    def fake_context(database, season, weeks):
        assert season == 2026 and weeks == list(range(1, 23))
        return {"status": "success", "records_received": 3,
                "records_accepted": 3, "records_rejected": 0, "errors": []}

    record = WeeklyJob().run({"db": db, "now": NOW, "context_fn": fake_context})
    assert record.status == JobStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Retrain: challenger-only, leakage-gated
# ---------------------------------------------------------------------------

def _retrain_db(db):
    db["models"].insert_one({"version": "MNB-NFL-2026.01", "champion": True})
    db["games"].insert_one({"season": 2025, "week": 18, "status": "final"})
    return db


def test_retrain_registers_challenger_never_champion(db, tmp_path):
    from pipelines.jobs.retrain import RetrainJob

    _retrain_db(db)

    def fake_train(version):
        out = tmp_path / version
        out.mkdir(parents=True)
        return out

    record = RetrainJob().run({
        "db": db, "now": NOW, "train_fn": fake_train,
        "version": "MNB-NFL-2026.99",
        "last_training_data": {"season": 2024, "week": 18},
    })
    assert record.status == JobStatus.SUCCEEDED
    challenger = db["models"].find_one({"version": "MNB-NFL-2026.99"})
    assert challenger is not None
    assert challenger["champion"] is False
    assert challenger["role"] == "challenger"
    assert challenger["promoted"] is False
    assert (tmp_path / "MNB-NFL-2026.99" / "challenger.json").exists()
    # Champion untouched.
    champ = db["models"].find_one({"champion": True})
    assert champ["version"] == "MNB-NFL-2026.01"
    assert record.detail["champion_unchanged"] == "MNB-NFL-2026.01"


def test_retrain_blocked_by_leakage_harness(db, tmp_path):
    from pipelines.jobs.retrain import RetrainJob

    _retrain_db(db)

    def leaking_train(version):
        raise AssertionError("kickoff rule violated: future score in frame")

    record = RetrainJob().run({
        "db": db, "now": NOW, "train_fn": leaking_train,
        "version": "MNB-NFL-2026.99",
        "last_training_data": {"season": 2024, "week": 18},
    })
    assert record.status == JobStatus.FAILED
    assert "leakage" in (record.error or "").lower()
    assert db["models"].find_one({"version": "MNB-NFL-2026.99"}) is None


def test_retrain_skips_when_no_new_finals(db, tmp_path):
    from pipelines.jobs.retrain import RetrainJob

    _retrain_db(db)
    with pytest.raises(SkipJob, match="no new final games"):
        RetrainJob().run({
            "db": db, "now": NOW,
            "train_fn": lambda version: tmp_path,
            "version": "MNB-NFL-2026.99",
            "last_training_data": {"season": 2025, "week": 18},
        })


def test_next_challenger_version_increments(tmp_path):
    from pipelines.jobs.retrain import _next_challenger_version

    (tmp_path / "MNB-NFL-2026.02").mkdir()
    (tmp_path / "MNB-NFL-2026.03").mkdir()
    (tmp_path / "not-a-version").mkdir()
    assert _next_challenger_version(tmp_path, 2026) == "MNB-NFL-2026.04"
    assert _next_challenger_version(tmp_path, 2027) == "MNB-NFL-2027.01"


# ---------------------------------------------------------------------------
# Admin observability endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_client(db, monkeypatch):
    from app.routers import admin as admin_router

    monkeypatch.setattr(admin_router, "_db", lambda: db)
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    from app.main import app

    return TestClient(app)


def test_admin_scheduler_jobs_lists_roster(admin_client, db):
    r = admin_client.get("/api/admin/scheduler/jobs",
                         headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200
    names = {j["name"] for j in r.json()["jobs"]}
    assert {"daily_ingest", "odds_pull", "postgame_settlement",
            "weekly_context", "retrain"} <= names
    job = next(j for j in r.json()["jobs"] if j["name"] == "daily_ingest")
    assert job["next_run_at"] is not None
    assert job["consecutive_failures"] == 0
    assert job["missed"] is False  # fresh deploy: no ticks older than the gap


def test_admin_scheduler_runs_and_alerts(admin_client, db, runner):
    db["scheduler_runs"].insert_one({
        "run_id": "r1", "job_name": "daily_ingest", "started_at": NOW,
        "finished_at": NOW, "status": "succeeded", "duration_seconds": 1.0,
        "detail": {}, "error": None,
    })
    db["scheduler_alerts"].insert_one({
        "alert_id": "a1", "job_name": "daily_ingest", "kind": "job_failed",
        "message": "boom", "run_id": "r1", "created_at": NOW,
    })
    h = {"X-Admin-Key": "test-admin-key"}
    runs = admin_client.get("/api/admin/scheduler/runs", headers=h).json()
    assert runs["count"] == 1 and runs["runs"][0]["run_id"] == "r1"
    alerts = admin_client.get("/api/admin/scheduler/alerts", headers=h).json()
    assert alerts["count"] == 1 and alerts["alerts"][0]["kind"] == "job_failed"
    filtered = admin_client.get("/api/admin/scheduler/runs?job_name=nope",
                                headers=h).json()
    assert filtered["count"] == 0


def test_admin_scheduler_endpoints_gated(admin_client, monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert admin_client.get("/api/admin/scheduler/jobs").status_code == 501
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    assert admin_client.get("/api/admin/scheduler/jobs").status_code == 403
    assert admin_client.get("/api/admin/scheduler/jobs",
                            headers={"X-Admin-Key": "wrong"}).status_code == 403


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def test_emit_alert_persists_without_webhook(db, monkeypatch):
    from pipelines.alerts import emit_alert

    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    alert = emit_alert(db, "daily_ingest", "job_failed", "boom")
    assert alert["webhook_delivered"] is False
    stored = db["scheduler_alerts"].find_one({"alert_id": alert["alert_id"]})
    assert stored is not None and stored["message"] == "boom"


def test_emit_alert_never_raises_without_db(monkeypatch):
    from pipelines.alerts import emit_alert

    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    alert = emit_alert(None, "daily_ingest", "run_missed", "no runs lately")
    assert alert["kind"] == "run_missed"
