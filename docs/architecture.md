# MONEY BY NUMBERS — Architecture

## Product

MONEY BY NUMBERS is a free-first NFL intelligence platform with
sportsbook-affiliate monetization. It publishes data-driven NFL game
analysis; revenue comes from affiliate referrals to sportsbooks, not from
selling picks. No fabricated data, odds, predictions, affiliate URLs, or
backtest numbers are ever acceptable anywhere in this codebase.

## Monorepo layout

```
money-by-numbers/
├── backend/        # Python/FastAPI API: health endpoints, models, MongoDB
│                   # indexes, Phase 2 nflverse ingestion (data_providers/,
│                   # ingestion/, scripts/ingest_nfl.py)
├── frontend/       # Next.js web app              (scaffolding; pages land in later phases)
├── ml/             # ML package: features, models, ensemble, calibration,
│                   #   versioning, leakage harness  (Phase 1: interface stubs)
├── pipelines/      # Background jobs: scheduler primitives + daily, weekly,
│                   #   postgame, retrain job stubs (Phase 1: not wired up)
├── docs/           # This documentation
├── docker-compose.yml
├── .env.example
├── README.md
└── .gitignore
```

## Suggested production architecture (per product spec §67)

| Layer      | Choice                                   | Role                                              |
|------------|------------------------------------------|---------------------------------------------------|
| Frontend   | Next.js                                  | Public site, dashboards, affiliate link pages     |
| Backend    | Python / FastAPI                         | REST API, admin endpoints, auth                   |
| Database   | MongoDB                                  | Games, predictions, grading records, users        |
| Cache      | Redis                                    | Hot prediction payloads, rate limiting            |
| Jobs       | Celery / background workers              | Daily/weekly/postgame/retrain pipelines           |
| Auth       | Google OAuth                             | User sign-in (session-based)                      |
| Payments   | Stripe                                   | Starter / Pro / Agency subscriptions (phase 9)    |

## Data flow (target state)

```
nflverse (free, no auth) ──┐
odds API ──────────────────┼─▶ ingestion ─▶ MongoDB ─▶ feature build ─▶ model ─▶ predictions
injury/weather providers ──┘                                    │                    │
                                                                ▼                    ▼
                                                     leakage harness           calibration
                                                                │                    │
                                                                └──────▶ API ◀───────┘
                                                                           │
                                                              ┌────────────┼────────────┐
                                                              ▼            ▼            ▼
                                                           Next.js      Redis        Celery
                                                           frontend     cache        workers
```

Notes:

- **nflverse is the default NFL data provider**: free, no authentication.
- **Odds require `ODDS_API_KEY`**; without it, market features are unavailable.
- **Injury and weather features are phase 5**; they need `INJURY_API_KEY` /
  `WEATHER_API_KEY` when implemented.
- **Numby (AI assistant) is phase 7**; needs `AI_PROVIDER` / `AI_API_KEY`.
- **Subscriptions are phase 9**; need Stripe keys.
- **Affiliate sportsbook config lives in the admin/database, never in env
  vars.** Only the disclosure text is env-configurable (optional).

## What is built — and what is not

Built (Phases 1–2):

- `backend/` FastAPI service: `GET /api/health`, `GET /api/data/health`
  (honest ingestion health), Pydantic models (`backend/app/models/core.py`),
  MongoDB index definitions (`backend/app/database.py`), auth scaffolding.
- `ml/` package: module layout, docstrings, `NotImplementedError` stubs,
  the `MNB-NFL-YYYY.NN` version format and metadata dataclass, and the
  leakage-harness interface with the kickoff rule encoded.
- `pipelines/` package: `JobRecord` dataclass, abstract `Job` base class,
  and stub jobs for daily / weekly / postgame / retrain.
- **Phase 2 — NFL data ingestion** (`backend/app/data_providers/`,
  `backend/app/ingestion/`, `backend/scripts/ingest_nfl.py`):
  - `NFLDataProvider` interface (auth, rate limiting, retry/backoff,
    caching, error handling, fetch/freshness tracking).
  - nflverse provider: free, no API key; downloads schedules + play-by-play
    parquet releases, normalizes legacy team abbreviations (LA/STL→LAR,
    SD→LAC, OAK→LV), aggregates per-team game stats (yards, turnovers,
    third downs, red zone, EPA, success rate); missing values stay `None`.
  - Validators: required fields, canonical teams, score/date sanity,
    no-final-scores-in-the-future, chronological consistency, stat ranges,
    stale-timestamp detection; corrupt records are rejected, never
    silently accepted.
  - Ingestion pipeline: batch duplicate detection, MongoDB upserts
    (idempotent re-runs), and `ingestion_runs` audit records with
    received/accepted/rejected counts and errors.
  - CLI: `python3 scripts/ingest_nfl.py --seasons 2016-2026` against
    MongoDB (`MONGODB_URI`) or `--mongomock` for a dependency-free run.
  - 32 tests in `tests/test_ingestion.py` covering validation, duplicates,
    normalization, aggregation, idempotency, audit records, null
    preservation, and the health endpoint.
- `docs/`: this file, deployment, and API docs.
- `ml/artifacts/`: trained model artifacts (per version) + the
  `backtest_2016_2025.json` walk-forward results (Phase 3).
- `docker-compose.yml`, `.env.example`, `README.md`, `.gitignore`.

## Phase 3 — ML model (built 2026-09-09)

- `ml/data_loader.py`: normalized games from the nflverse parquet via the
  Phase 2 provider cache (same bytes as ingestion — one source of truth).
- `ml/features.py`: point-in-time features — margin-aware Elo (538-style,
  K=20, HFA=35 fit), last-5 scoring form, streaks, rest differential,
  divisional/dome/neutral flags, head-to-head, week. Chronological pass
  with same-kickoff batching; `_data_through` audit column per row.
  Market lines are deliberately NOT features.
- `ml/models.py`: LightGBM + XGBoost + sklearn-HGB wrappers, uniform
  interface; `available_learners()` reports what is installed (this
  environment: LightGBM + HGB — XGBoost could not install).
- `ml/ensemble.py`: stacking classifier (logistic meta-learner on
  chronological out-of-fold predictions) + ridge-weighted regressors.
- `ml/calibration.py`: Brier, log-loss, calibration curves,
  isotonic-regression calibrator. Stored predictions are calibrated.
- `ml/leakage.py`: `KickoffLeakageHarness` — frame audit, truncation
  invariance, per-game as-of checks; `assert_no_leakage` gates every run.
- `ml/backtest.py`: 2016–2025 walk-forward with fold cutoff audits and an
  Elo baseline in the same harness; ATS graded vs nflverse closing lines.
- `ml/train.py`: trains `MNB-NFL-2026.02` on 2016–2025 finals, persists
  versioned artifacts + metadata.
- `backend/app/routers/predictions.py`: `GET /api/predictions`,
  `/api/predictions/models`, `/api/predictions/backtests` — honest empty
  states until a training run stores records.
- `tests/test_ml.py`: 20 tests (Elo math, feature correctness, leakage
  audits, calibration, versioning, schema, estimator smoke tests, ATS
  sign-convention grading test, predictions-endpoint honest states).

Measured walk-forward (2016–2025, 2026-09-09): winner accuracy 59.47%
vs 62.16% in-harness Elo baseline; Brier 0.2371 vs 0.2344; margin MAE
10.57; ATS vs closing lines 50.66% (n=2,574). The ensemble did not beat
the Elo baseline — recorded honestly in `ml/artifacts/` and the model
metadata; no improvement is claimed.

Not built (explicitly out of scope for Phases 1–3):

- No odds ingestion (needs `ODDS_API_KEY`).
- No injury/weather ingestion (phase 5).
- No scheduler wiring (Celery/workers) — ingestion runs on demand via the
  CLI until the scheduler is built; no Redis caching layer.
- No auth (Google OAuth), no Stripe, no affiliate link plumbing.
- No frontend pages beyond scaffolding.

Anything claiming otherwise is a bug — please report it.

## Phase 5 — injuries + weather (built 2026-09-09)

- `backend/app/data_providers/injuries.py`: `NflverseInjuryProvider` — free,
  no API key. Downloads per-season parquet from the nflverse `injuries`
  release
  (`https://github.com/nflverse/nflverse-data/releases/tag/injuries`,
  assets `injuries_{season}.parquet`, 2009+), curl transport with retries +
  disk cache. Normalizes player/team/week, game and practice statuses, and
  injury fields; missing values stay `None`; bad rows are reported, never
  silently dropped. **Coverage is honest**: the official *weekly* injury
  report — not a live news wire; `coverage()` says so explicitly. Unknown
  `report_status` values (e.g. `"Note"`, observed in the real 2024 data) are
  preserved verbatim, never rewritten.
- `backend/app/data_providers/weather.py`: `OpenMeteoWeatherProvider` —
  free, no API key. Kickoff-hour temperature, wind, gusts, precipitation
  probability, conditions at the venue (`timezone=UTC`, nearest-hour
  matching). Games beyond the ~16-day forecast horizon are skipped with a
  recorded reason (never backfilled with fake history). Venue coordinates
  come from `backend/app/data_providers/stadiums.py` (curated stadium map
  with sources; no ambiguous city-name geocoding). Dome games get `dome:
  true` and null outdoor measurements — nulls stay null. `roof` is taken
  from the nflverse schedule when present; "retractable"/unknown is kept
  unknown, never guessed.
- `backend/app/ingestion/validators.py`: `validate_injury_record` (invalid
  team/status/week, missing name, future `date_modified` rejected),
  `injury_warnings` (unknown statuses accepted with warnings),
  `validate_weather_record` (missing fields, bad coordinates, implausible
  temp/wind/precip ranges; null measurements allowed).
- `backend/app/ingestion/pipeline.py`: `ingest_injuries` / `ingest_weather` /
  `run_context_ingestion` — validation, batch dedup (injuries:
  `(season, week, gsis_id)`; weather: `game_id`), idempotent MongoDB upserts,
  and `ingestion_runs` audit docs with received/accepted/rejected/errors.
- `backend/scripts/ingest_injuries_weather.py`: CLI (`--seasons`, `--weeks`,
  `--mongomock`, `--mongo-uri`).
- `backend/app/routers/game_context.py`: `GET /api/games` and
  `GET /api/games/{game_id}/context` — injuries + weather with honest
  `"unavailable"` / `"no_context"` / `"no_games"` sections; an empty injury
  list is never presented as "no injuries".
- `GET /api/data/health`: freshness is now **dataset-specific** — a
  dataset's `last_run_id` is the most recent run whose `records_accepted`
  covers that dataset; a games run never makes injuries look fresh.
- `ml/features.py`: documented **hooks only** — `CONTEXT_FEATURE_NAMES`,
  `injury_context_features()`, `weather_context_features()` return neutral
  defaults and are NOT model inputs. Spec §72 requires a full 2016–2025
  walk-forward before any context feature enters training; the Elo champion
  is unchanged.
- `frontend/app/nfl/page.tsx`: real NFL hub — season/week slate, expandable
  per-game injury report (summary counts + player table) and kickoff weather
  card, with exact `DATA UNAVAILABLE` empty states.
- `tests/test_injury_weather.py`: 35 tests (parsing, unknown statuses,
  validation, duplicates, idempotency, staleness, dome/retractable handling,
  forecast-range skips, API states with/without DB).

Not built (explicitly out of scope for Phase 5):

- No scheduler wiring — context ingestion runs on demand via the CLI.
- No context features in the model (hooks only, pending walk-forward
  validation per spec §72).
- No MongoDB Atlas in this environment — production persistence still needs
  `MONGODB_URI` (see `docs/deployment.md`).

## Phase 6 — track record (built 2026-09-09)

- `backend/app/picks/ledger.py`: `lock_pick()` — the ONLY sanctioned writer
  of the `predictions` collection. Refuses writes after kickoff, refuses
  duplicates per (game_id, model_version, market), schema-validates every
  row. Insert-only.
- `backend/app/picks/settle.py`: `grade_pick()` (moneyline win/loss/push,
  spread cover/push/miss vs home-perspective line, totals over/under/push,
  Brier for moneyline) + `run_settlement()` — idempotent post-game grading
  into `pick_results` (upsert by `prediction_id`, never double-counts),
  CLV (pick line/price vs latest pre-kickoff odds snapshot), and
  `settlement_runs` audit docs. Never touches the `predictions` ledger.
- New indexes: `predictions(game_id, model_version, market)` unique,
  `pick_results(prediction_id)` unique, plus season/week, model_version,
  confidence indexes and `settlement_runs(run_id)` unique.
- `backend/app/routers/track_record.py`: `GET /api/track-record/summary`
  (record, accuracy, ROI over priced picks only, Brier, per-market split),
  `/tiers`, `/calibration`, `/picks` (pending picks shown pre-resolution),
  and `GET /api/methodology` — real walk-forward numbers read from the
  on-disk artifacts, never hardcoded; includes the research writeup when it
  lands. Zero write routes: immutability is structural.
- `backend/scripts/settle_picks.py`: CLI (`--mongomock`, `--mongo-uri`,
  `--season`, `--week`).
- `pipelines/jobs/postgame.py`: implemented — delegates to
  `run_settlement`, fails honestly without a database.
- Frontend: real `track-record` page (stat cards, tier table, calibration
  buckets, recent picks with pending states, honest empty states) and a
  methodology section on `how-it-works` presenting the 2016–2025 results
  win-or-lose (ensemble 59.47% vs Elo 62.16%, ATS 50.7%).
- `tests/test_track_record.py`: 42 tests (ledger discipline, grading,
  idempotency, CLV, no-write-routes, endpoint math, methodology honesty,
  postgame job).

Not built (explicitly out of scope for Phase 6):

- No scheduler wiring — settlement runs on demand via the CLI/job until
  the scheduler is built.
- Live picks require a real game week + MongoDB: the ledger is empty until
  the first pre-kickoff lock.

## Phase 7 — Numby AI assistant (built 2026-09-09)

- `backend/app/numby/`: `retrieval.py` (grounding — team/intent detection,
  fact bundle drawn only from real collections + on-disk artifacts, every
  fact sourced), `guardrails.py` (guarantee/chasing/minor refusals,
  responsible-gambling disclaimer, AI identity), `providers.py`
  (`AIProvider` interface; `OpenAICompatibleProvider` via curl,
  `AI_PROVIDER`/`AI_API_KEY`/`AI_MODEL` env; honest not-configured
  fallback), `memory.py` (`conversations` collection, 1000-char / 20-turn
  history / 60-message session caps), `responder.py` (guardrails ->
  retrieval -> LLM-or-deterministic-templates -> disclaimer).
- `backend/app/routers/numby.py`: `POST /api/numby/chat`,
  `GET /api/numby/history`, `GET /api/numby/status`. Chat persists when a
  database is configured, works statelessly (and says so) without one.
- `backend/app/models/core.py`: `Conversation`/`ChatMessage`; new
  `conversations` indexes in `database.py`.
- Frontend: real `/numby` chat page (message thread, suggested questions,
  session persistence in localStorage, basic/LLM mode banner, DATA
  UNAVAILABLE when the backend is unreachable) + typed client in
  `frontend/lib/api.ts`.
- `tests/test_numby.py`: 37 tests (detection, grounding verbatimness,
  no-fabrication incl. explicit "ask with no data" cases, guardrail
  refusals, disclaimer logic, memory caps, provider interface, LLM
  failure fallback). Also fixed a stale Phase 6 test assumption now that
  the parallel research task delivered `METHODOLOGY_PUBLIC.md`.

Not built (explicitly out of scope for Phase 7):

- No LLM key in this environment — production LLM mode needs `AI_API_KEY`.
- No scheduler wiring; no changes to the champion model.

## Phase 8 — affiliate (built 2026-09-09)

The platform's primary revenue mechanism, built with total honesty: the
`sportsbooks` collection starts EMPTY, and no partnership, URL, commission,
or revenue figure is ever invented.

- `backend/app/models/business.py`: `Sportsbook` (name, affiliate_url,
  terms, commission_structure, active; URL validated http(s)-or-null, never
  invented) and `Conversion` (transcribed from real affiliate reports;
  revenue null = not reported, never zero-as-performance).
- `backend/app/database.py`: `sportsbooks` (sportsbook_id unique) and
  `conversions` (conversion_id unique) indexes.
- `backend/app/routers/affiliate.py` (public):
  - `GET /api/affiliate/disclosure` — disclosure text, env-configurable via
    `AFFILIATE_DISCLOSURE_TEXT`, honest default.
  - `GET /api/affiliate/cta-status` — server-side CTA gate: `ctas_enabled`
    is true only when an ACTIVE sportsbook has a configured URL. Names only;
    URLs never leak to the client.
  - `GET /r/{sportsbook_id}` — tracked referral: logs the click to
    `affiliate_clicks` (reusing the Phase 1 `build_click_record` helper),
    then 302-redirects to the configured URL verbatim. Unknown id, inactive
    partner, or missing URL -> 404 — never a guessed URL.
- `backend/app/routers/admin.py` — admin CRUD gated by `X-Admin-Key`
  vs `ADMIN_API_KEY` (constant-time compare; 501 when unconfigured, 403 on
  mismatch; no default key, no bypass): list/create/patch/deactivate
  sportsbooks (DELETE = deactivate, history preserved), record/list
  conversions, and `GET /api/admin/affiliate/stats` — aggregates from real
  logged data only, with honest empty states (`has_data: false` + notes;
  revenue unknown shown as unknown, not zero).
- `backend/app/config.py`: `AFFILIATE_DISCLOSURE_TEXT`, `ADMIN_API_KEY`;
  `.env.example` documents both.
- Frontend: `best-bets` page fetches cta-status — with zero partners no CTA
  buttons render at all (the existing honest note stays); when partners
  exist, a "Ready to place a bet?" panel lists sportsbooks linking to the
  backend's tracked redirect (`rel="sponsored nofollow"`). Footer fetches
  the disclosure from the API with the bundled constant as fallback.
- `tests/test_affiliate_phase8.py`: 24 tests (redirect logging + exact-URL
  302, 404 paths, CTA gating, disclosure default/configured, admin
  gate/CRUD/URL validation, stats honesty incl. unknown-revenue, and a
  repo-wide scan asserting no hardcoded affiliate-looking URLs in shipped
  code).

Not built (explicitly out of scope for Phase 8):

- No sportsbook partnerships exist — the table is empty until the admin
  configures real ones.
- No user-auth-linked admin roles yet (Phase 11 admin panel); the shared
  admin key is the interim gate.
- Numby answers and game views render no CTAs today; when they do, they
  must consult the same cta-status gate.

## Phase 9 — Billing / Stripe subscriptions (spec 51-54)

Subscriptions are SECONDARY revenue (affiliate is primary). Plans:
Free / Starter / Pro / Agency. Free keeps the full predictions,
edges, and track-record experience; paid plans buy higher Numby
question limits (20/day free), CSV export of the settled pick ledger,
and bet alerts — all enforced server-side.

- `backend/app/billing/`:
  - `plans.py` — plan catalog + limits + rank comparisons. No prices
    stored: `price_id_for_plan()` reads env price IDs; `plan_for_price_id()`
    reverse-maps Stripe prices to plans.
  - `identity.py` — requester identity from the signed `mbn_session`
    cookie (Google sub else email); `get_plan()` reads the
    `subscriptions` collection (active/trialing only, else free);
    `check_numby_quota()` / `record_numby_usage()` back the Numby daily
    limits via the `usage_counters` collection.
  - `stripe_client.py` — minimal Stripe REST client over the `curl`
    binary (Python HTTP stalls through this sandbox's egress proxy).
    Secret key passed via curl `--config` on stdin, never argv/URLs/logs.
  - `webhooks.py` — `Stripe-Signature` HMAC-SHA256 verification
    (5-min tolerance), idempotent event application via `stripe_events`,
    handlers for checkout/subscription/invoice events. The ONLY writer of
    subscription plan/status.
- `backend/app/routers/billing.py` — `/api/billing/status|checkout|
  portal|webhook`, `/api/billing/export/picks` (CSV), `/api/billing/alerts`
  CRUD. `config.py` gains `STRIPE_PRICE_ID_STARTER/PRO/AGENCY` and
  `FRONTEND_URL`; `.env.example` documents both.
- `backend/app/routers/numby.py` — chat enforces daily quotas server-side
  when a DB is configured (`quota_exceeded` envelope with upgrade nudge);
  stateless and ungated without a DB.
- Frontend: `app/pricing/page.tsx` (real plan cards, current-plan display,
  Stripe Checkout/Portal flows, DATA UNAVAILABLE price states),
  `lib/api.ts` billing client (`formatPrice` never invents a number),
  Numby page renders the quota-exceeded upgrade nudge.
- `tests/test_billing.py`: 56 tests — signature verification (valid/wrong/
  tampered/stale/malformed), webhook 501/400 paths, idempotency,
  subscription lifecycle (created/deleted/payment_failed), checkout
  501/401/422/502 paths, portal 404/ok, export 401/402/CSV content, alerts
  401/402/CRUD/ownership, plan helpers, Numby quota integration.
- Honesty notes: prices are fetched from Stripe or reported unavailable;
  no subscriptions/payments/revenue exist until keys are configured;
  past_due/canceled subscriptions fall back to the free tier (no silent
  cutoff of the core product); no card details ever stored.

Not built (explicitly out of scope for Phase 9): no Stripe keys, webhook
secret, or price IDs configured — billing is complete but dormant.
Phase 10 (automation) remains: scheduler wiring for ingestion, odds pulls,
settlement, and retrain jobs.

## Phase 10 — automation (built 2026-09-09, spec 55-58)

The manual scripts and job stubs are now a reliable scheduled operation.

- `pipelines/schedule.py` — the job roster with cadences (all UTC),
  season helpers (`current_season`, `in_nfl_season`: Sep 1–Feb 15), and
  pure due-ness functions (`last_tick`/`next_tick`/`is_due` for daily,
  weekly-days, and monthly cadences). No database, no network: fully
  unit-tested.
- `pipelines/runner.py` — `SchedulerRunner`: the restart-safety core.
  - **Locking:** one lock document per job in `scheduler_jobs`, acquired
    with a single atomic `find_one_and_update` (upsert). Two runners can
    never hold the same lock, so a job cannot double-run after a restart —
    the restarted process sees the fresh lock and stands down (records a
    "lock held" skip).
  - **Crash visibility:** locks go stale after `SCHEDULER_STALE_MINUTES`
    (default 120). The next runner reclaims the lock, marks the orphaned
    `scheduler_runs` record `"crashed"`, and emits an alert — the gap is
    visible, never silently ignored.
  - **Audit trail:** every attempt appends a `scheduler_runs` record
    (`run_id` unique): start/end, status (`succeeded`/`failed`/`skipped`/
    `crashed`), duration, detail, error. Skips carry their reason.
  - **Missed-run detection:** each job has a `max_gap_hours`; no
    successful run inside the window (and in season when required) fires
    one `run_missed` alert per gap until a success clears it.
  - **Alerting:** `pipelines/alerts.py` persists every alert to
    `scheduler_alerts`, logs it as structured JSON, and POSTs to
    `ALERT_WEBHOOK_URL` when configured. Alerting never fails a job.
  - All datetimes are normalized to naive UTC at the MongoDB boundary
    (pymongo decodes stored datetimes as naive UTC).
- `pipelines/run_scheduler.py` — CLI: `--once` (cron/systemd: run due
  jobs, exit), `--loop` (daemon), `--job NAME [--force]`,
  `--check-missed`, `--status`, `--list`. Requires `MONGODB_URI` —
  deliberately no in-memory mode; scheduler state must survive restarts.
- Job wiring (spec 56-57), each delegating to the real Phase 2-6 code:
  - `daily_ingest` (daily 10:00 UTC, year-round): nflverse ingestion for
    the current season + games-stored health count in the run detail.
  - `odds_pull` (Tue/Fri/Sun 12:00 UTC, in-season): The Odds API pull with
    the Phase 4 credit-budget check *before* any network call; missing key
    or exhausted budget → honest skip, never a pull.
  - `postgame_settlement` (Mon/Tue 13:00 UTC, in-season): the Phase 6
    settlement (idempotent; ledger untouched).
  - `weekly_context` (Wed 11:00 UTC, in-season): injury + weather refresh
    via the Phase 5 pipeline (stored schedule preferred, nflverse
    fallback).
  - `retrain` (monthly, 1st 07:00 UTC): gated by the leakage harness
    (`AssertionError` fails the job, registers nothing) and by a
    freshness check (skips when no new final games). Registers the new
    version with `champion: false, role: "challenger"` plus a
    `challenger.json` marker, then verifies the champion is
    byte-identical before/after — **the scheduler can never promote a
    model**.
- Graceful degradation everywhere: no key / no budget / no database / out
  of season / no new data → recorded skip or honest failure. Nothing is
  ever invented to fill a gap.
- `GET /api/admin/scheduler/jobs|runs|alerts` (existing admin-key gate):
  live operational status for operators; the Phase 11 admin panel can
  build its UI on these.
- `tests/test_automation.py`: 31 tests — pure scheduling math, lock
  contention, stale-lock reclaim + crash marking, sequential-rerun run
  records, due-ness, out-of-season skips, missed-run alert-once semantics,
  failure alerts + consecutive-failure counting/reset, per-job behavior
  (ingest season, no-DB failure, odds skip without key, retrain
  challenger-only/leakage-block/freshness-skip/version increment),
  admin endpoint gating, alert persistence.

Not built (explicitly out of scope for Phase 10):

- No hosted-scheduler adapter (cron/daemon is the production approach).
- No `ALERT_WEBHOOK_URL` configured — alerts persist to DB + logs.
- Admin UI built (Phase 11).
- Scheduler has never run against production (no `MONGODB_URI` in this
  environment); cadences are designed, tested, and ready.

## Phase 11 — admin panel (built 2026-09-09, spec 59-61)

Backend additions (on the existing `X-Admin-Key` gate, unchanged contract):
`GET /api/admin/subscriptions/overview` — plan/status aggregates from the
real `subscriptions` collection; revenue always null (never estimated).

Frontend (`frontend/`): `lib/admin.ts` (typed admin client, sessionStorage
key handling, `X-Admin-Key` header injection, honest 501/403/unreachable
states), `components/AdminGuard.tsx` + `components/AdminShell.tsx` (session
guard, section nav, sign-out clearing the key), and the `app/admin/` routes:
`login` (key entry, validated before storage), `page.tsx` (overview cards —
data health, model registry, scheduler, affiliate, subscriptions — all
pulled live, all with honest empty states), `sportsbooks` (partner CRUD —
create defaults to no URL so CTAs stay disabled), `scheduler` (jobs with
last/next run and failure flags, run history with job filter, alerts),
`subscriptions` (read-only aggregates), `models` (read-only registry —
promotion is deliberately not exposed).

Safety invariants (tested in `tests/test_admin_phase11.py`):
- Every admin route: 501 when `ADMIN_API_KEY` unset, 403 on wrong key —
  parametrized over all 11 admin routes, no bypass.
- Admin responses never contain the key or other secrets; the public
  CTA-status endpoint names partners but never exposes affiliate URLs.
- No route anywhere has a write method on a `predictions` path, and the
  admin router source never touches the `predictions` collection — settled
  picks cannot be rewritten through the admin panel.
- `lock_pick()` remains the only writer of pre-kickoff predictions with its
  kickoff cutoff enforced.

## Phase 12 — final testing, security, deployment (built 2026-09-09)

**Security middleware** (`backend/app/middleware.py`, wired in `main.py`
via `configure_cors`):

- `SecurityHeadersMiddleware` — nosniff, DENY framing, strict referrer,
  locked-down permissions policy, API-tight CSP, and HSTS in production.
- `RateLimitMiddleware` — per-IP sliding window on `/api/numby/chat` and
  `/r/` (env-tunable; 429 + `Retry-After`; preflights exempt). Per-process
  state: a gateway limit is recommended in front for hard guarantees.
- CORS — explicit origins from `CORS_ORIGINS`; wildcard refused at boot in
  production.

**Security posture as of 2026-09-09:**

- OSV dependency scan: Python app deps clean; npm lock clean after
  `postcss` 8.4.31 → 8.5.28 (npm `overrides` pin). `requests` advisories
  exist only in the sandbox system env, not in the app's dependency tree.
- Secret scan: clean — no committed secrets, no private keys, no
  credential/PII logging.
- AuthN/Z: admin endpoints behind `X-Admin-Key` (constant-time compare,
  501 unconfigured, 403 mismatch, no default key); Stripe webhooks
  signature-verified + idempotent; model promotion is not exposed as a
  button (registry is read-only).
- 12 security tests in `tests/test_security_phase12.py` (headers, HSTS
  gating, CORS allow/deny, wildcard refusal, rate-limit 429 + Retry-After,
  preflight exemption, headers on 429s).

**Deployment:** `docs/deployment.md` now carries the full production
runbook — Vercel (frontend), Railway/Render (backend), MongoDB Atlas,
scheduler cron modes, the environment-variable checklist with
what-breaks-without-what, the ordered user launch checklist, and the
honest gaps list. **Nothing is deployed as of 2026-09-09**; the gaps
list names every user action still required (Atlas, Odds API key, Stripe,
OAuth, AI key, admin key, affiliate partnerships, backups, Open-Meteo
terms review).

**What is built — and what is not (final):** everything in the 12-phase
spec is implemented and tested (247+ tests at last full run plus the 10
new Phase 12 security tests). What remains is entirely on the user's
side: accounts, keys, partnerships, hosting, and the first production
ingestion/scheduler runs. The codebase degrades honestly at every
missing integration — it will never fabricate data to fill a gap.
