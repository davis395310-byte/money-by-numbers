# MBN stack monitor

Our own uptime + correctness checker. $0 forever, no vendor accounts, no
90-day logins.

## How it works

1. **Detection** — `.github/workflows/stack-check.yml` runs
   `ops/monitor/check_stack.py` every 15 minutes on GitHub Actions
   (free for public repos, external to Render/Vercel). Checks:
   - backend `/api/health` reachable, app + database ok
   - scheduler alive (latest `weekly_picks` run didn't fail/crash —
     the dead-man's-switch for the silent scheduler crash)
   - frontend returns HTTP 200
   - current week's board present (16-ish picks; bye weeks vary)
   - **no-post-kickoff invariant**: every pick's `created_at` is before
     its `kickoff`
2. **Alerting** — on failure the workflow files/updates a GitHub issue
   labeled `monitoring-alert` (idempotent: one open issue at a time,
   auto-closed on recovery).
3. **Notification** — a watcher forwards new `monitoring-alert` issues
   to Richard (chat + push).

## The lock-deadline rule

The Tuesday lock job runs at 14:00 UTC; Thursday games kick off ~00:15
UTC Friday. The checker requires the board to be complete starting 24h
before the week's earliest kickoff. Before that it's "pending", not a
failure — so the Tuesday window never false-alarms.

## Backend support

- `GET /api/health` → `checks.scheduler` reports the latest
  `weekly_picks` run from `scheduler_jobs` (time, status, error).
- `GET /api/predictions` exposes `kickoff` publicly so the checker can
  verify the no-post-kickoff rule without credentials.
