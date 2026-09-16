#!/usr/bin/env python3
"""MONEY BY NUMBERS scheduler entry point (Phase 10, spec 55).

Two production modes:

    # Cron / systemd: run whatever is due, then exit (every 5-15 min).
    python pipelines/run_scheduler.py --once

    # Self-contained daemon: tick internally.
    python pipelines/run_scheduler.py --loop --interval 300

Also useful for operators:

    python pipelines/run_scheduler.py --list            # roster + next runs
    python pipelines/run_scheduler.py --job daily_ingest --force
    python pipelines/run_scheduler.py --check-missed
    python pipelines/run_scheduler.py --status

Requires MONGODB_URI: scheduler locks and run history must survive
restarts, so there is deliberately no in-memory mode.

Run from the repo root so ``pipelines`` is importable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def _get_db():
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        print("ERROR: MONGODB_URI is not set. The scheduler needs a durable "
              "database for locks and run history; it has no in-memory mode.",
              file=sys.stderr)
        sys.exit(2)
    from pymongo import MongoClient

    db_name = os.environ.get("MONGODB_DB") or "money_by_numbers"
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    return client[db_name]


def main() -> int:
    parser = argparse.ArgumentParser(description="MONEY BY NUMBERS job scheduler.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="run due jobs once and exit (for cron/systemd)")
    mode.add_argument("--loop", action="store_true",
                      help="run as a daemon, ticking every --interval seconds")
    mode.add_argument("--job", metavar="NAME",
                      help="run a single scheduled job by name")
    mode.add_argument("--list", action="store_true",
                      help="print the job roster with next scheduled runs")
    mode.add_argument("--check-missed", action="store_true",
                      help="run missed-run detection only")
    mode.add_argument("--status", action="store_true",
                      help="print operational status of all jobs")
    parser.add_argument("--interval", type=int, default=300,
                        help="daemon tick interval in seconds (default: 300)")
    parser.add_argument("--force", action="store_true",
                        help="with --job: bypass due-ness (lock still applies)")
    parser.add_argument("--stale-minutes", type=int, default=None,
                        help="lock staleness horizon (default: SCHEDULER_STALE_MINUTES or 120)")
    args = parser.parse_args()

    from pipelines.runner import SchedulerRunner
    from pipelines.schedule import JOB_SCHEDULES, utcnow

    if args.list:
        from pipelines.schedule import next_tick

        now = utcnow()
        for entry in JOB_SCHEDULES:
            print(f"{entry['name']:20s} next: {next_tick(entry['cadence'], now)} "
                  f"{'(in-season only)' if entry['in_season_only'] else ''}")
            print(f"{'':20s} {entry['description']}")
        return 0

    db = _get_db()
    runner = SchedulerRunner(db, stale_minutes=args.stale_minutes)

    if args.check_missed:
        alerts = runner.check_missed_runs()
        print(json.dumps({"missed_alerts": len(alerts),
                          "jobs": [a["job_name"] for a in alerts]}, indent=2))
        return 0

    if args.status:
        print(json.dumps(runner.all_job_statuses(), indent=2, default=str))
        return 0

    if args.job:
        record = runner.run_job(args.job, force=args.force)
        print(json.dumps({"job": args.job, "status": record.status.value,
                          "error": record.error, "detail": record.detail},
                         indent=2, default=str))
        return 0 if record.status.value in ("succeeded", "skipped") else 1

    if args.once:
        records = runner.run_due_jobs()
        summary = [{"job": r.job_id, "status": r.status.value, "error": r.error}
                   for r in records]
        print(json.dumps(summary, indent=2, default=str))
        return 0 if all(r.status.value != "failed" for r in records) else 1

    # --loop daemon
    print(f"scheduler daemon started (interval={args.interval}s); Ctrl-C to stop",
          flush=True)
    try:
        while True:
            try:
                records = runner.run_due_jobs()
                for r in records:
                    print(json.dumps({"job": r.job_id, "status": r.status.value,
                                      "error": r.error}, default=str), flush=True)
            except Exception as exc:  # noqa: BLE001 - daemon never dies on a bad tick
                logging.getLogger("mbn.scheduler").exception(
                    "scheduler tick failed: %s", exc)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("scheduler daemon stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
