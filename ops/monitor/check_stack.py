#!/usr/bin/env python3
"""MBN stack checker — external uptime + correctness monitor.

Runs on GitHub Actions (free, external to Render/Vercel) every 15 minutes.
Exits 0 when every check passes, 1 when anything fails. Prints a JSON
summary plus human-readable lines.

What it checks (the failure modes Richard has actually hit):
  1. Backend API reachable and healthy (/api/health: app ok, database ok).
  2. Scheduler alive: the latest weekly_picks run did not fail/crash
     (dead-man's-switch for the silent scheduler crash).
  3. Frontend reachable (HTTP 200).
  4. Weekly picks present and fresh for the current target week.
  5. No-post-kickoff invariant: every pick was locked before its kickoff.

Design notes:
  - The "target week" is the most recently locked (season, week); the
    script derives it from the data rather than duplicating the
    scheduler's week-selection logic.
  - A lock deadline of (earliest kickoff - 24h) avoids false alarms in the
    Tuesday-lock window: before the deadline a missing board is "pending",
    after it a missing/incomplete board is a failure.
  - Minimum board size is 13 (bye weeks can drop below 16 games).
  - Stdlib only (plus the curl binary for proxy-reliable fetching).
    No installs, no secrets.
"""

from __future__ import annotations

import datetime as dt
import json
import sys

BACKEND = "https://mnb-backend-sh83.onrender.com"
FRONTEND = "https://money-by-numbers.vercel.app"
MIN_BOARD = 13  # bye weeks can drop below 16 games
LOCK_DEADLINE_HOURS = 24  # board must be locked this long before first kickoff


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _get(url: str, timeout: int):
    """GET url -> (status_code or None, body).

    Uses curl via subprocess: this sandbox's proxy makes Python HTTP
    clients (urllib/httpx) stall intermittently, while curl transfers
    reliably. Stdlib-only otherwise; curl ships on the VM and on
    GitHub Actions runners.
    """
    import subprocess
    import tempfile
    import os

    tmp = tempfile.NamedTemporaryFile(delete=False, prefix="mbn_check_")
    tmp.close()
    try:
        proc = subprocess.run(
            [
                "curl", "-sS", "-L", "-m", str(timeout),
                "-o", tmp.name, "-w", "%{http_code}",
                "-A", "mbn-stack-check/1.0", url,
            ],
            capture_output=True, text=True, timeout=timeout + 15,
        )
        code = proc.stdout.strip()[-3:]
        try:
            with open(tmp.name, "rb") as fh:
                body = fh.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        if not code.isdigit():
            return None, "curl error: %s" % proc.stderr.strip()[:200]
        return int(code), body
    except Exception as exc:  # curl missing, timeout, ...
        return None, "transport error: %s" % exc
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _parse_ts(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _in_season(now: dt.datetime) -> bool:
    # Regular season runs ~Sept through the first week of January.
    return now.month in (9, 10, 11, 12) or (now.month == 1 and now.day <= 10)


def main() -> int:
    now = _now()
    checks = []
    failed = False

    def record(name, ok, detail, warning=False):
        checks.append(
            {"name": name, "result": "pass" if ok else "fail", "detail": detail}
        )
        line = ("PASS" if ok else ("WARN" if warning else "FAIL")) + " " + name + ": " + detail
        print(line, flush=True)
        return ok or warning

    # 1 + 2. Backend health + scheduler state.
    code, body = _get(BACKEND + "/api/health", 90)
    if code != 200:
        failed = not record("backend", False, "GET /api/health -> %s (%s)" % (code, body[:120]))
    else:
        try:
            health = json.loads(body)
        except Exception as exc:
            failed = not record("backend", False, "health body not JSON: %s" % exc)
            health = None
        if health is not None:
            ok = health.get("status") == "ok"
            db_ok = (health.get("checks", {}).get("database") or {}).get("status") == "ok"
            sched = (health.get("checks", {}).get("scheduler") or {})
            sched_status = sched.get("status")
            detail = "status=%s db=%s scheduler=%s/%s" % (
                health.get("status"),
                (health.get("checks", {}).get("database") or {}).get("status"),
                sched_status,
                sched.get("last_status"),
            )
            if not record("backend", ok and db_ok, detail):
                failed = True
            if sched_status == "error":
                if not record(
                    "scheduler",
                    False,
                    "weekly_picks last_status=%s err=%s"
                    % (sched.get("last_status"), sched.get("last_error")),
                ):
                    failed = True
            elif sched_status == "unknown":
                record("scheduler", True, "no scheduler_jobs record yet", warning=True)
            else:
                record("scheduler", True, "last_status=%s at %s" % (sched.get("last_status"), sched.get("last_run_at")))

    # 3. Frontend.
    code, _ = _get(FRONTEND, 30)
    if not record("frontend", code == 200, "GET / -> %s" % code):
        failed = True

    # 4 + 5. Picks: presence, freshness, no-post-kickoff invariant.
    code, body = _get(BACKEND + "/api/predictions?limit=200", 90)
    if code != 200:
        if not record("picks", False, "GET /api/predictions -> %s" % code):
            failed = True
    else:
        try:
            payload = json.loads(body)
        except Exception as exc:
            payload = None
            if not record("picks", False, "predictions body not JSON: %s" % exc):
                failed = True
        if payload is not None:
            picks = payload.get("predictions") or []
            if not picks:
                if _in_season(now):
                    if not record("picks", False, "no predictions stored during season"):
                        failed = True
                else:
                    record("picks", True, "no predictions (offseason)", warning=True)
            else:
                latest = max(picks, key=lambda p: str(p.get("created_at") or ""))
                season, week = latest.get("season"), latest.get("week")
                board = [p for p in picks if p.get("season") == season and p.get("week") == week]
                n = len(board)
                kickoffs = [k for k in (_parse_ts(p.get("kickoff")) for p in board) if k]
                created = [c for c in (_parse_ts(p.get("created_at")) for p in board) if c]

                # Universal invariant: no pick locked at/after its kickoff.
                violations = [
                    p.get("prediction_id")
                    for p, c, k in zip(board,
                                      (_parse_ts(p.get("created_at")) for p in board),
                                      (_parse_ts(p.get("kickoff")) for p in board))
                    if c and k and c >= k
                ]
                if violations:
                    if not record("picks", False, "POST-KICKOFF LOCKS: %s" % ", ".join(violations[:8])):
                        failed = True
                elif not kickoffs:
                    record("picks", True, "kickoff not exposed by backend yet; count=%d" % n, warning=True)
                    if n < MIN_BOARD and not record("picks-count", False, "board has %d picks" % n):
                        failed = True
                elif all(k > now for k in kickoffs):
                    earliest = min(kickoffs)
                    if now > earliest - dt.timedelta(hours=LOCK_DEADLINE_HOURS):
                        if not record("picks", n >= MIN_BOARD,
                                       "season %s week %s past lock deadline: %d picks" % (season, week, n)):
                            failed = True
                    else:
                        record("picks", True, "season %s week %s in lock window (%d picks so far)" % (season, week, n),
                               warning=(n < MIN_BOARD))
                else:
                    if not record("picks", n >= MIN_BOARD,
                                   "season %s week %s in progress/past: %d picks" % (season, week, n)):
                        failed = True

    summary = {
        "checked_at": now.isoformat(),
        "failed": failed,
        "checks": checks,
    }
    print("SUMMARY " + json.dumps(summary))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
