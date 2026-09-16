# MONEY BY NUMBERS — $0 Launch Runbook

> Launch the full product without spending anything. Every service below
> has a free tier that covers a year-one audience. Free-tier policies
> change over time — confirm on each site before signing up.

## The $0 stack

| Piece | Service | Free tier | Honest tradeoff |
|---|---|---|---|
| Database | MongoDB Atlas M0 | Free forever, 512 MB, one cluster per project | 512 MB cap — plenty for picks/ledgers; odds snapshots stay lean |
| Frontend | Vercel Hobby | Free, generous bandwidth | None meaningful at our scale |
| Backend | Render Free | 750 hrs/mo | **Sleeps after 15 min idle** — first visitor after idle waits ~30–60 s |
| Scheduler | GitHub Actions | 2,000 min/mo free | Plenty for 5–15 min cron pings |
| Domain | `*.vercel.app` subdomain | Free | No custom domain until there's revenue (~$12/yr later) |

Optional paid later, not needed day one: custom domain, Atlas M10,
always-on backend, Stripe (year two), AI key (Numby runs in basic mode).

## Richard's steps (only you can do these — they need your email)

1. **Atlas**: sign up at mongodb.com/cloud/atlas → Build a Cluster →
   choose **M0 Sandbox (Free)** → create a database user → Network Access →
   allow `0.0.0.0/0` (or Render's IPs) → copy the connection string.
   Set a **billing alert at $0** (Organization → Alerts) so nothing can
   ever charge you silently.
2. **Vercel**: sign up with GitHub → Import the `Bpenergy` repo →
   select the `frontend/` directory → deploy. Note the `*.vercel.app` URL.
3. **Render**: sign up with GitHub → New Web Service → same repo →
   Dockerfile in `backend/` → deploy on the **Free** plan.
4. Send Rosie: the Atlas connection string (via the Secure Vault, never
   chat), the Vercel URL, and the Render URL.

That's it. No credit card required for any of the three free tiers.

## Rosie's steps (once the above land)

1. Set production env vars on Render (`MONGODB_URI`, `CORS_ORIGINS`,
   `NEXT_PUBLIC_API_URL`, Odds API key from the vault).
2. Apply Mongo indexes, ingest NFL data + Week 1 locked picks.
3. Add the GitHub Actions cron to hit the scheduler endpoint.
4. Verify end-to-end: health checks, picks page, track record, best bets.
5. Hand over the live URL.

## What "free" costs us (stated plainly)

- Cold starts: the backend sleeps when idle. Mitigation: the frontend
  shows honest loading states; cron pings keep it warm during game days.
- If the audience outgrows M0/Render-free, that's a *good* problem —
  it means the following exists, and the affiliate revenue pays for
  the upgrade.
