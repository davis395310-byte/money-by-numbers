# MBN stack monitor

Our own uptime + correctness checker. $0 forever, no vendor accounts, no
90-day logins.

## How it works

1. **Detection** — `check_stack.py` runs every 15 minutes and hits only
   public endpoints (no secrets). It runs on Rosie's scheduler today;
   `stack-check.yml.github-workflow` is the same check as a GitHub Actions
   workflow (free for public repos, external to Render/Vercel) — to enable
   it, save that file as `.github/workflows/stack-check.yml`. That push
   needs a GitHub token with `workflow` scope (ours doesn't have it yet).
2. **Checks**:
   - backend `/api/health` reachable, app + database ok
   - scheduler alive (latest `weekly_picks` run didn't fail/crash —
     the dead-man's-switch for the silent scheduler crash)
   - frontend returns HTTP 200
   - current week's board present (13+ picks; bye weeks vary)
   - **no-post-kickoff invariant**: every pick's `created_at` is before
     its `kickoff`
3. **Notification** — on any failure Richard is notified directly
   (chat + push); the failure detail names exactly what broke.

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
