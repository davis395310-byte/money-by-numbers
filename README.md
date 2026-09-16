# MONEY BY NUMBERS

Free-first NFL intelligence, monetized through sportsbook-affiliate
referrals — not through selling picks. The platform publishes data-driven
NFL game analysis; revenue comes from affiliate partnerships with
sportsbooks.

> **Honesty rule (hard requirement):** this codebase never fabricates data,
> odds, predictions, affiliate URLs/relationships, revenue, backtest
> results, or model accuracy. Skeletons contain docstrings and TODOs, never
> placeholder numbers presented as real.

## Monorepo layout

```
money-by-numbers/
├── backend/        # Python/FastAPI API + Phase 2 nflverse ingestion
├── frontend/       # Next.js web app              (scaffolding)
├── ml/             # ML package: features, models, ensemble, calibration,
│                   #   versioning (MNB-NFL-YYYY.NN), leakage-test harness
├── pipelines/      # Background jobs: restart-safe scheduler (Phase 10)
│                   # + daily/odds/postgame/weekly/retrain jobs
├── docs/           # architecture.md, deployment.md, api.md
├── docker-compose.yml
├── .env.example
└── README.md
```

## Quickstart

### With Docker Compose

```bash
cp .env.example .env   # then fill in REQUIRED CONFIG values (see below)
docker compose build
docker compose up -d
```

- Backend: http://localhost:8000 (`GET /api/health`, `GET /api/data/health`)
- Frontend: http://localhost:3000
- MongoDB: local `mongo:7` with a named volume (dev only)

### Local development (no Docker)

Backend (from `backend/`):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

> The ingestion provider downloads nflverse parquet releases with the
> `curl` binary — make sure `curl` is installed.

Frontend (from `frontend/`):

```bash
cd frontend
npm install    # when package.json is present
npm run dev
```

ML / pipelines (repo root):

```bash
python3 -m pytest tests/ -q   # once tests exist
```

## NFL data ingestion (Phase 2)

nflverse is the free, no-auth NFL data provider. From `backend/`:

```bash
# Full history into MongoDB (uses MONGODB_URI):
python3 scripts/ingest_nfl.py --seasons 2016-2026

# Dependency-free smoke run (in-memory; nothing persists):
python3 scripts/ingest_nfl.py --seasons 2024-2024 --mongomock
```

The run upserts `teams` (32 canonical teams), `games` (schedules +
scores), and `game_stats` (per-team aggregates from play-by-play: yards,
turnovers, third downs, red zone, EPA, success rate), and writes an audit
record to `ingestion_runs` with received/accepted/rejected counts.
Missing source values stay `None` and surface as DATA UNAVAILABLE
downstream; corrupt records are rejected, never silently accepted.
Verify with `GET /api/data/health`. See
[docs/deployment.md](docs/deployment.md) §3b for the full runbook.

## Tests

```bash
python3 -m pytest tests/ -q
```

Covers Phase 1 scaffolding, the Phase 2 ingestion suite
(`tests/test_ingestion.py`: validation, duplicates, normalization, PBP
aggregation, idempotency, audit records, health endpoint), and the Phase 3
ML suite (`tests/test_ml.py`: Elo math, feature correctness, leakage
audits, calibration, versioning, prediction schema, estimator smoke
tests), and the Phase 8 affiliate suite (`tests/test_affiliate_phase8.py`:
tracked redirects, CTA gating, disclosure, admin CRUD, stats honesty,
no-hardcoded-URL scan), the Phase 9 billing suite
(`tests/test_billing.py`: webhook signature verification, idempotency,
subscription lifecycle, checkout/portal flows, plan-gated export and
alerts, Numby quotas — all against synthetic Stripe objects, no real
account touched), and the Phase 10 automation suite
(`tests/test_automation.py`: scheduling math, restart-safe locking,
stale-lock reclaim with crash marking, missed-run detection, graceful
skips, challenger-only retraining with leakage gating, admin
observability endpoints).

## Machine learning (Phase 3)

The model is a calibrated stacking ensemble (LightGBM + sklearn
HistGradientBoosting; XGBoost/CatBoost are used when installed) over
point-in-time features: margin-aware Elo, recent scoring form, streaks,
rest differential, situational flags. Market lines are never inputs.

```bash
python3 -m ml.run_backtest   # 2016-2025 walk-forward -> ml/artifacts/backtest_2016_2025.json
python3 -m ml.train          # train MNB-NFL-2026.02 -> ml/artifacts/MNB-NFL-2026.02/
python3 -m ml.register_model --version MNB-NFL-2026.02   # store in MongoDB (needs MONGODB_URI)
```

Every training/evaluation run is gated by the leakage harness
(`ml/leakage.py`): a historical prediction may only use information
available strictly before kickoff. Any violation raises before metrics
are recorded. ATS is graded against nflverse closing lines only.

**Measured walk-forward (2016–2025, run 2026-09-09, `ml/artifacts/backtest_2016_2025.json`):**
winner accuracy 59.47% vs 62.16% Elo baseline (in-harness) and 61.4%
preliminary v1 (2025 only); Brier 0.2371 vs 0.2344; margin MAE 10.57;
ATS vs closing lines 50.66% (n=2,574); O/U 49.39% (n=2,618).
**No improvement claim:** the ensemble did not beat the Elo baseline,
and shows no edge against closing lines. These are the real measured
numbers — the model is registered as `MNB-NFL-2026.02` with this
comparison recorded in its metadata.

## Docs

- [docs/architecture.md](docs/architecture.md) — monorepo layout, production
  architecture, data flow, and what Phases 1–2 do/don't include.
- [docs/deployment.md](docs/deployment.md) — build, env vars, database,
  NFL ingestion runbook (§3b), scheduler, webhooks, domain, monitoring,
  backups.
- [docs/api.md](docs/api.md) — endpoints (`GET /api/health`,
  `GET /api/data/health`) with example honest responses.

## Remaining configuration

You need accounts/credentials for each of these before the matching feature
works. Nothing here ships with secrets — see `.env.example`.

| Needed for              | Account / credential                                  | Status   |
|-------------------------|-------------------------------------------------------|----------|
| Production database     | MongoDB connection string (`MONGODB_URI`)             | Required |
| Google sign-in          | Google Cloud OAuth client ID/secret + redirect URI, `SESSION_SECRET` | Required |
| Live odds               | Odds provider API key + base URL                      | Required |
| Subscriptions (phase 9) | Stripe secret key, webhook secret, 3 price IDs        | Required |
| Injury reports (phase 5)| Injury provider API key                               | Optional |
| Weather (phase 5)       | Weather provider API key                              | Optional |
| Numby AI (phase 7)      | AI provider API key                                   | Optional |
| Analytics               | Analytics provider                                    | Optional |
| Affiliate links         | Sportsbook affiliate partnerships (configured in admin/database, not env) | Per partnership |
