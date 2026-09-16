# MONEY BY NUMBERS — Deployment

> Every step that needs an external account, API key, or credential is
> marked **REQUIRED CONFIG**. Nothing in this repo ships with secrets;
> see `.env.example`. **The platform is not deployed anywhere as of
> 2026-09-09** — this document is the runbook for getting it there.

## 1. Build

```bash
# From the repo root:
docker compose build        # builds backend + frontend images
docker compose up -d        # starts mongodb, backend, frontend (local dev)
```

Local development (without Docker) is per-service; see `README.md`.

Production hosting (recommended, §9):

| Service  | Host            | Notes                                              |
|----------|-----------------|----------------------------------------------------|
| Frontend | Vercel          | Connect repo; set `NEXT_PUBLIC_API_URL` at build   |
| Backend  | Railway / Render| Dockerfile in `backend/`; set env vars (§2)        |
| Database | MongoDB Atlas   | M10+ recommended; see §3                           |
| Scheduler| Railway cron / Render cron / VPS cron | `pipelines/run_scheduler.py --once` every 5–15 min |

## 2. Environment variables

1. Copy `.env.example` to `.env` (dev) or set them in your host's secret
   manager (production — **REQUIRED CONFIG**).
2. All values are empty by default; there are no working defaults for
   anything that needs a credential.

| Group      | Variables                                                        | Required |
|------------|------------------------------------------------------------------|----------|
| Database   | `MONGODB_URI`, `MONGODB_DB`                                      | Prod: **REQUIRED CONFIG** |
| Auth       | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_AUTH_REDIRECT_URI`, `SESSION_SECRET` | **REQUIRED CONFIG** for sign-in |
| Security   | `ENVIRONMENT` (`development`/`production`), `CORS_ORIGINS`        | Prod: **REQUIRED CONFIG** (`CORS_ORIGINS` must name the real frontend domain(s); wildcard refused at boot in production) |
| Rate limits| `RATE_LIMIT_NUMBY_PER_MIN` (default 30), `RATE_LIMIT_REDIRECT_PER_MIN` (default 60) | Optional |
| NFL data   | `NFL_DATA_PROVIDER` (default `nflverse`, free, no auth)           | Optional |
| Odds       | `ODDS_API_KEY`, `ODDS_API_BASE_URL`                              | **REQUIRED CONFIG** for live odds/edges |
| AI (Numby) | `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`                          | Optional (Numby runs in honest basic mode without) |
| Stripe     | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_STARTER`, `STRIPE_PRICE_ID_PRO`, `STRIPE_PRICE_ID_AGENCY` | **REQUIRED CONFIG** for subscriptions |
| Affiliate  | `AFFILIATE_DISCLOSURE_TEXT`                                      | Optional (per-sportsbook URLs live in admin/database, not env) |
| Admin      | `ADMIN_API_KEY`                                                  | **REQUIRED CONFIG** for admin endpoints |
| Frontend   | `NEXT_PUBLIC_API_URL` (build-time), `FRONTEND_URL` (backend, for Stripe redirects) | Prod: **REQUIRED CONFIG** |
| Analytics  | `ANALYTICS_PROVIDER`                                             | Optional |

### What breaks without what

| Missing                        | Degraded behavior (all honest, never fabricated) |
|--------------------------------|--------------------------------------------------|
| `MONGODB_URI`                  | Backend boots; every data endpoint returns `not_configured` / `no_data` states |
| `ODDS_API_KEY`                 | No live odds; edges unavailable; Best Bets empty |
| Stripe vars                    | Pricing page shows DATA UNAVAILABLE; checkout 501s honestly |
| `AI_API_KEY`                   | Numby answers from deterministic grounded templates ("basic mode" banner) |
| Google OAuth vars              | Sign-in disabled; app remains fully usable anonymously |
| `ADMIN_API_KEY`                | Admin endpoints return 501; no partner/URL management possible |
| Affiliate partnerships (DB)    | Zero CTAs render anywhere; referral endpoint 404s |

## 3. Database setup + indexes (MongoDB Atlas runbook)

1. **REQUIRED CONFIG**: create an Atlas project + cluster (M10 minimum for
   production; M0 free tier works for staging).
2. Create a database user with read/write on the app database; whitelist
   the backend host's IPs (or use VPC peering / private endpoint).
3. Set `MONGODB_URI` (Atlas connection string) and `MONGODB_DB`
   (e.g. `money_by_numbers`) in the backend environment.
4. Apply the index set defined in `backend/app/database.py` before first
   boot — it covers `teams`, `games`, `game_stats`, `ingestion_runs`,
   `predictions`, `pick_results`, `odds_snapshots`, `edges`,
   `sportsbooks`, `affiliate_clicks`, `conversions`, `subscriptions`,
   `conversations`, `users`, and scheduler collections.
5. Verify: `GET /api/data/health` reports `"mongodb": "ok"`.

## 3b. NFL data ingestion (Phase 2)

nflverse is free and needs no API key. From `backend/`:

```bash
python3 scripts/ingest_nfl.py --seasons 2016-2026   # full history -> MongoDB
python3 scripts/ingest_nfl.py --seasons 2024-2024 --mongomock  # smoke run
```

System dependency: the provider downloads parquet with the `curl` binary.
Audit records land in `ingestion_runs`; re-runs are idempotent.

## 3c. Injuries + weather ingestion (Phase 5)

Both free, no API key:

```bash
python3 scripts/ingest_injuries_weather.py --seasons 2024-2025 --weeks 1-18
```

## 4. Scheduler setup (Phase 10 — implemented)

The scheduler runs jobs defined in `pipelines/schedule.py` (daily/weekly
ingest, odds pulls Tue/Fri/Sun in season, postgame settlement, weekly
retrain evaluation).

**Production options (pick one):**

- **Railway/Render cron:** run `python pipelines/run_scheduler.py --once`
  every 10 minutes. Requires `MONGODB_URI` (locks + run history are
  durable; there is deliberately no in-memory mode).
- **VPS cron:** same command via system cron.
- **Daemon:** `python pipelines/run_scheduler.py --loop --interval 300`
  under systemd/supervisor.

Operators: `--list` (roster + next runs), `--job <name> --force`,
`--check-missed`, `--status`. Alerts fire per `pipelines/alerts.py`
(webhook-configured) on repeated failures.

## 5. Webhook setup (Stripe)

- **REQUIRED CONFIG**: expose `https://<domain>/api/billing/webhook`,
  register it in the Stripe dashboard, set `STRIPE_WEBHOOK_SECRET`.
- Every event is signature-verified and processed idempotently; unsigned
  payloads are rejected. Never trust client-reported subscription state.

## 6. Domain + TLS

- **REQUIRED CONFIG**: point DNS at the host / load balancer, terminate
  TLS (HTTPS only in production), set `GOOGLE_AUTH_REDIRECT_URI` to the
  production callback and `CORS_ORIGINS` to the production frontend
  origin(s). `ENVIRONMENT=production` enables HSTS and refuses wildcard
  CORS at boot.

## 7. Monitoring

- Health probes: `GET /api/health` (service) and `GET /api/data/health`
  (dependencies). Alert on non-`ok`.
- Ship container logs to your aggregator; alert on repeated scheduler
  failures (`pipelines` run history `status == "failed"`).
- Uptime monitoring on the public site from an external checker.
- **REQUIRED CONFIG**: accounts/credentials for the chosen monitoring and
  logging providers.

## 8. Backup strategy (per product spec §70)

- **MongoDB**: Atlas scheduled snapshots (daily minimum), tested restores —
  an untested backup is not a backup.
- **`.env` / secrets**: production secret manager only, never in the repo,
  never in unencrypted backups.
- **Model artifacts**: versioned `MNB-NFL-YYYY.NN`; retain artifact +
  metadata for every version ever served (predictions stay reproducible).
- **REQUIRED CONFIG**: backup destination and retention policy.

## 9. Hosting runbooks

### 9a. Frontend — Vercel

1. Import the repo in Vercel; set root directory to `frontend/`.
2. Build env: `NEXT_PUBLIC_API_URL=https://<backend-domain>` —
   **REQUIRED CONFIG** (baked at build time; rebuild after changing).
3. Deploy; point your domain at the Vercel project.

### 9b. Backend — Railway (or Render)

1. New service from repo, Dockerfile path `backend/Dockerfile`.
2. Set every §2 variable in the service's environment (use Railway's
   secret storage, not the repo).
3. Start command is the Dockerfile default:
   `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
4. Health check path: `/api/health`.
5. Production note: the in-process rate limiter is per-worker — put a
   gateway/WAF limit in front for hard guarantees.

### 9c. First production run order

1. Atlas cluster → `MONGODB_URI` set on backend.
2. Deploy backend; verify `/api/health` → ok and `/api/data/health` →
   mongodb ok.
3. Run NFL ingestion (2016–2026), then injuries/weather ingestion.
4. Start the scheduler cron; verify `--status` shows jobs scheduled.
5. Deploy frontend with `NEXT_PUBLIC_API_URL` pointing at the backend.
6. Configure Stripe / OAuth / Odds / AI / admin keys as accounts are
   created (each feature degrades honestly until then).

## 10. Launch checklist (user actions, in order)

- [ ] MongoDB Atlas cluster created; `MONGODB_URI` set → unlocks: all data endpoints, predictions, ledger, track record
- [ ] Google Cloud OAuth client created; redirect URI set → unlocks: sign-in
- [ ] The Odds API account created; `ODDS_API_KEY` set → unlocks: live odds, edges, Best Bets
- [ ] Sportsbook affiliate partnerships signed; URLs added via admin → unlocks: CTA buttons, referral revenue
- [ ] Stripe account + products/prices created; webhook registered → unlocks: subscriptions
- [ ] AI provider key set (optional) → unlocks: full Numby conversational mode
- [ ] `ADMIN_API_KEY` generated (`openssl rand -hex 32`) → unlocks: admin endpoints
- [ ] `ENVIRONMENT=production`, `CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`, `FRONTEND_URL`, TLS, DNS
- [ ] First ingestion runs (NFL → injuries/weather), scheduler cron started
- [ ] Backup snapshots scheduled and restore tested
- [ ] Open-Meteo commercial-use terms reviewed (Phase 5 weather data)

## 11. Honest gaps (as of 2026-09-09)

- No production hosting is configured; nothing is deployed.
- No `MONGODB_URI`, `ODDS_API_KEY`, Stripe keys, OAuth credentials, AI
  key, or `ADMIN_API_KEY` are set anywhere.
- No sportsbook affiliate partnerships exist; the partners table is
  intentionally empty and all CTAs are disabled.
- The pick ledger is empty until the first real pre-kickoff pick lock;
  track-record pages show honest empty states.
- Rate limiting is per-process (in-memory); multi-worker deployments need
  a gateway-level limit for hard guarantees.
- The LLM HTTP path for Numby's AI provider has not been exercised
  against a live endpoint (no key in any environment); basic mode is the
  tested path.
