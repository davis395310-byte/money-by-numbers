# MONEY BY NUMBERS — API

Phase 1 exposes the service health endpoint; Phase 2 adds honest
data-ingestion health. Both return honest status: they report what is
actually configured and reachable, never fabricated data.

---

## `GET /api/health`

Backend service health check.

**Example request**

```http
GET /api/health HTTP/1.1
Host: localhost:8000
```

**Example honest response (healthy)**

```json
{
  "status": "ok",
  "service": "money-by-numbers-backend",
  "version": "0.1.0",
  "timestamp": "2026-09-09T12:00:00Z"
}
```

**Example honest response (degraded)** — e.g. database unreachable:

```json
{
  "status": "degraded",
  "service": "money-by-numbers-backend",
  "version": "0.1.0",
  "timestamp": "2026-09-09T12:00:00Z",
  "checks": {
    "database": "unreachable"
  }
}
```

---

## `GET /api/data/health`

Data-ingestion health (Phase 2): real MongoDB collection counts for the
`teams`, `games`, `game_stats`, `injuries`, `odds`, and `weather` datasets,
plus freshness and the latest ingestion run from the `ingestion_runs`
collection. When `MONGODB_URI` is not configured, every dataset reports
`"not_yet_ingested"` with zero counts — never a fake "ok".

Per dataset:

- `status`: `"ok"` (has records), `"not_yet_ingested"` (zero records),
  or an error entry in the top-level `errors` list.
- `record_count`: real `count_documents` result.
- `last_ingestion`: ISO timestamp of the newest ingested record, or `null`.
- `freshness`: `"fresh"` / `"stale"` / `null`, based on per-dataset TTLs
  (teams: 30 days; games/game_stats/injuries/weather: 7 days; odds: 48 h).
- `last_run_id`: the most recent run whose `records_accepted` covers that
  dataset — dataset-specific, so a games run never claims the injuries
  dataset (Phase 5).

The top-level `ingestion` object carries the latest run (`run_id`,
`source`, `status`, `finished_at`, accepted/rejected counts) and the
run's first errors (max 10).

**Example honest response (after a Phase 2 nflverse ingestion)**

```json
{
  "teams": {
    "status": "ok",
    "last_ingestion": "2026-09-09T17:30:00+00:00",
    "record_count": 32,
    "freshness": "fresh",
    "last_run_id": "c9106188c5f849dd82ad46c2a0a8f945"
  },
  "games": {
    "status": "ok",
    "last_ingestion": "2026-09-09T17:30:00+00:00",
    "record_count": 3033,
    "freshness": "fresh",
    "last_run_id": "c9106188c5f849dd82ad46c2a0a8f945"
  },
  "game_stats": {
    "status": "ok",
    "last_ingestion": "2026-09-09T17:30:00+00:00",
    "record_count": 5522,
    "freshness": "fresh",
    "last_run_id": "c9106188c5f849dd82ad46c2a0a8f945"
  },
  "injuries": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "odds": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "weather": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "ingestion": {
    "last_run": {
      "run_id": "c9106188c5f849dd82ad46c2a0a8f945",
      "source": "nflverse",
      "status": "partial",
      "finished_at": "2026-09-09T17:35:00+00:00",
      "records_accepted": {"teams": 32, "games": 3033, "game_stats": 5522},
      "records_rejected": {"teams": 0, "games": 0, "game_stats": 0}
    },
    "freshness": "fresh",
    "recent_errors": [
      "pbp 2026 download failed: curl exit 28 ... (nflverse has not published a 2026 play-by-play file yet)"
    ]
  },
  "errors": []
}
```

> The counts above are from the verified 2026-09-09 ingestion run
> (seasons 2016–2026; the run was `partial` only because nflverse has not
> published `play_by_play_2026.parquet` yet — the 2026 season starts
> 2026-09-09). The endpoint always reports the numbers actually in the
> database.

**Example honest response (MongoDB not configured)**

```json
{
  "teams": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "games": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "game_stats": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "injuries": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "odds": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "weather": {"status": "not_yet_ingested", "record_count": 0, "freshness": null, "last_ingestion": null, "last_run_id": null},
  "errors": []
}
```

---

## `GET /api/predictions`

Latest stored predictions, newest first. Every row carries
`model_version` (`MNB-NFL-YYYY.NN`) and `training_cutoff`. Query params:
`season`, `week`, `limit` (default 32, max 200).

**Example honest response (no predictions stored yet)**

```json
{
  "status": "no_predictions",
  "reason": "no predictions stored yet",
  "predictions": []
}
```

**Example honest response (with predictions)**

```json
{
  "status": "ok",
  "predictions": [
    {
      "prediction_id": "abc123",
      "game_id": "2026_01_NE_SEA",
      "season": 2026,
      "week": 1,
      "home_team": "SEA",
      "away_team": "NE",
      "model_version": "MNB-NFL-2026.02",
      "training_cutoff": "2025 season (pre-kickoff Week 1 2026)",
      "model_probability": 0.7153,
      "predicted_winner": "SEA",
      "predicted_home_score": 26,
      "predicted_away_score": 21,
      "confidence": "high",
      "created_at": "2026-09-09T12:00:00+00:00",
      "actual_winner": null,
      "correct": null
    }
  ]
}
```

Result fields (`actual_winner`, `correct`) stay `null` until the game is
final. Losing picks are never deleted.

## `GET /api/predictions/models`

Registered model versions. Returns `"status": "no_models"` with an empty
list until a training run registers a version.

## `GET /api/predictions/backtests`

Recorded backtest runs with their real measured metrics (walk-forward
accuracy, Brier score, calibration, ATS vs closing lines). Returns
`"status": "no_backtests"` with an empty list until a backtest is
recorded. No backtest numbers are ever invented — if a backtest has not
run, the list is empty.

---

## `GET /api/odds/status`

Odds provider status (Phase 4). Reports whether an odds API key is
configured, remaining provider credits (from the `odds_credit_usage`
collection, or `null` when unrecorded), and the odds snapshot inventory.
Without a key it returns HTTP 200 with an honest `"odds_unavailable"`
status — never fabricated lines.

**Example honest response (no API key configured)**

```json
{
  "status": "odds_unavailable",
  "reason": "no API key configured"
}
```

**Example honest response (key configured, snapshots present)**

```json
{
  "status": "odds_available",
  "key_configured": true,
  "credits_remaining": 842,
  "last_snapshot_at": "2026-09-09T12:00:00",
  "snapshot_count": 3
}
```

`credits_remaining` is `null` until a credit reading is recorded; the
provider module is imported lazily, so this endpoint works even before the
odds provider half of Phase 4 is finished.

---

## `GET /api/odds/games`

Latest odds snapshot per game (Phase 4): the newest timestamp wins per
`game_id`. Query params: `season`, `week`.

**Example honest response (no snapshots stored yet)**

```json
{
  "status": "no_odds",
  "reason": "no odds snapshots stored yet",
  "games": []
}
```

**Example honest response (with snapshots)**

```json
{
  "status": "ok",
  "games": [
    {
      "game_id": "2026_01_NE_SEA",
      "timestamp": "2026-09-09T12:00:00",
      "sportsbook": "bk",
      "spread": -2.5,
      "moneyline_home": -110,
      "moneyline_away": -110,
      "total": 44.5
    }
  ]
}
```

---

## `GET /api/edges`

Model-vs-market edges (Phase 4). Joins the latest odds snapshot per game
with predictions from the **champion model** (explicit `champion: true`
flag in the `models` collection; `MNB-NFL-2026.01`, the Elo lineage, is the
fallback — walk-forward measured it at 62.16% vs 59.47% for the
`MNB-NFL-2026.02` ensemble, so the flag is never switched silently).
A side is flagged only when
`edge = model_probability − no_vig_implied_probability >= min_edge`
(query param, default `0.03`). Query params: `season`, `week`, `min_edge`.

Edge rows are computed — never stored or invented: they exist only when
both odds snapshots AND champion-model predictions are present.

**Example honest response (missing input)**

```json
{
  "status": "edges_unavailable",
  "reason": "missing: odds snapshots, predictions from champion model MNB-NFL-2026.01",
  "model_version": "MNB-NFL-2026.01",
  "edges": []
}
```

**Example honest response (edges found)**

```json
{
  "status": "ok",
  "model_version": "MNB-NFL-2026.01",
  "threshold": 0.03,
  "edges": [
    {
      "game_id": "2026_01_NE_SEA",
      "market": "moneyline",
      "side": "home",
      "line": null,
      "price": -110,
      "model_probability": 0.65,
      "novig_implied": 0.5,
      "edge": 0.15,
      "ev": 0.2409,
      "confidence": "MEDIUM",
      "model_version": "MNB-NFL-2026.01"
    }
  ]
}
```

Edge-row fields: `game_id`, `market` (`moneyline`; `spread`/`total` only
when the prediction carries per-market model probabilities — the stored
prediction schema does not, so those are never invented), `side`
(`home`/`away`/`over`/`under`), `line` (points line for spread/total,
`null` for moneyline), `price` (American odds), `model_probability`,
`novig_implied`, `edge`, `ev` (expected value of a unit stake:
`model_probability × decimal_odds − 1`), `confidence`
(`HIGH` ≥ 0.68 / `MEDIUM` ≥ 0.58 / `LOW`), and `model_version` (the version
actually used, always labeled).

When nothing meets the threshold:

```json
{
  "status": "no_edges",
  "reason": "no sides with edge >= 0.03",
  "model_version": "MNB-NFL-2026.01",
  "threshold": 0.03,
  "edges": []
}
```

---

## `GET /api/edges/clv`

Closing-line value (Phase 4): for graded predictions, the moneyline at
pick time (earliest snapshot at/after the prediction's `created_at`) vs the
closing line (latest snapshot at/before kickoff from `games.game_date`).
`clv_cents = pick_price − close_price` (positive means the pick beat the
close), aggregated as `{n, avg_clv_cents, beat_close_rate}`.

**Example honest response (no closing lines recorded yet)**

```json
{
  "status": "data_unavailable",
  "reason": "no closing lines recorded yet"
}
```

**Example honest response**

```json
{
  "status": "ok",
  "n": 128,
  "avg_clv_cents": 4.2,
  "beat_close_rate": 0.54
}
```

---

## `GET /api/games`

Games from the `games` collection with venue/kickoff/roof. Query params:
`season`, `week`, `upcoming` (future non-final games), `limit` (default 32).

```json
{
  "status": "ok",
  "games": [
    {
      "game_id": "2024_01_KC_BAL",
      "season": 2024, "week": 1,
      "game_date": "2024-09-05T20:20:00",
      "home_team": "KC", "away_team": "BAL",
      "venue": "Arrowhead Stadium", "roof": "outdoors", "status": "final"
    }
  ]
}
```

Empty states are honest on HTTP 200: no database -> `"status": "no_games",
"reason": "database not configured"`; nothing stored -> `"status":
"no_games", "reason": "no games stored yet"`. Never a guessed slate.

---

## `GET /api/games/{game_id}/context`

Official weekly NFL injury report rows (both teams, game week) plus the
kickoff-hour weather record for one game. Injuries come from the nflverse
injuries release (2009+, no API key); weather is the Open-Meteo record at
the venue (`weather` collection, ingested per game).

```json
{
  "status": "ok",
  "game_id": "2024_01_KC_BAL",
  "game": { "...": "game fields" },
  "injuries": {
    "status": "ok",
    "records": [
      {
        "season": 2024, "week": 1, "team": "KC", "position": "QB",
        "full_name": "Test Player", "report_status": "Questionable",
        "report_primary_injury": "Ankle",
        "practice_status": "Limited Participation in Practice"
      }
    ],
    "summary": {"Out": 0, "Doubtful": 0, "Questionable": 1, "Other": 0},
    "note": "Official weekly NFL injury report (nflverse release) — not a live news wire."
  },
  "weather": {
    "status": "ok",
    "record": {
      "temp_f": 72.5, "wind_mph": 8.0, "wind_gust_mph": 12.0,
      "precip_prob": 0.1, "conditions": "Partly cloudy",
      "dome": false, "source": "open-meteo"
    }
  }
}
```

Honest sections: no injury rows -> `"injuries": {"status": "unavailable",
"reason": "..."}` (never an empty list presented as "no injuries"); no
weather row -> `"weather": {"status": "unavailable", ...}` (never invented
temperatures). Weather nulls mean unknown — dome games have no outdoor
conditions. No database -> `"status": "no_context", "reason": "database not
configured"`; unknown `game_id` -> `"status": "no_context", "reason":
"unknown game_id: ..."`.

---

## Track record (Phase 6)

The public, immutable ledger of every published pick. Picks are locked via
`backend/app/picks/ledger.py::lock_pick` BEFORE kickoff (late or duplicate
writes are refused); post-game grading runs idempotently through
`backend/app/picks/settle.py::run_settlement` (or `python3
scripts/settle_picks.py --mongomock`) into the `pick_results` collection.
The `predictions` ledger is never modified after kickoff, and the API
exposes **no** PUT/PATCH/DELETE routes for it — losing picks cannot be
edited or removed.

### `GET /api/track-record/summary`

Overall record computed live from the ledger: wins/losses/pushes,
accuracy, units ROI (only over picks with a real recorded price —
picks without prices are counted in `roi_excluded_no_price`, never
estimated), average Brier, and a per-market split. Honest empty states:
`"no_data"` (no database), `"no_settled_picks"` (with `pending_picks`).

### `GET /api/track-record/tiers`

Accuracy by confidence tier (`HIGH`/`MEDIUM`/`LOW`), computed from the
ledger — never hardcoded.

### `GET /api/track-record/calibration`

Moneyline picks bucketed by predicted probability: average predicted vs
observed win rate per bucket.

### `GET /api/track-record/picks?result=win`

Recent locked picks with settlement status (`pending` picks are shown
BEFORE they resolve) and results once graded. Optional `result`
filter (`win`/`loss`/`push`/`ungraded`).

### `GET /api/methodology`

The real measured 2016–2025 walk-forward numbers, read from the on-disk
training artifacts (`ml/artifacts/backtest_2016_2025.json`,
`ml/artifacts/MNB-NFL-2026.02/metadata.json`) — never hardcoded. Includes
per-season accuracy, the Elo-baseline comparison, the leakage-harness
status, honest limits, and the independent research writeup when the
parallel research task delivers `METHODOLOGY_PUBLIC.md`
(`research_note_status: "pending …"` until then).

---

## Numby chat (Phase 7)

Numby is the platform's AI assistant. Every answer is grounded ONLY in
stored platform data (predictions, odds/edges, injuries, weather, the
track-record ledger, the on-disk methodology artifacts). When data is
missing, Numby says so — it never invents a stat, line, or prediction.
Guardrails refuse guaranteed-win requests, chasing-losses encouragement,
and betting advice to minors; betting-related answers carry a
responsible-gambling disclosure.

Without `AI_API_KEY`, Numby runs in **basic mode**: deterministic
grounded templates (no LLM call). With a key, an OpenAI-compatible chat
endpoint (`AI_PROVIDER`, default `openai`; model via `AI_MODEL`, default
`gpt-4o-mini`) answers under a strict grounding system prompt, with
automatic fallback to the templates if the LLM call fails.

### `POST /api/numby/chat`

Body: `{"message": str, "session_id": str | null, "user_id": str | null}`.
Returns `{"status": "ok", "reply": str, "sources": ["kind:source", …],
"ai_mode": "basic"|"llm"|"basic_fallback"|"guardrail",
"disclaimer": str | null, "session_id": str | null, "persistent": bool}`.
Messages over 1000 chars are rejected; sessions cap at 60 messages.

### `GET /api/numby/history?session_id=…`

Recent messages for a session (capped at the 20 most recent turns).

### `GET /api/numby/status`

Honest provider status: `{"ai_configured": bool, "mode": "llm"|"basic", …}`.

---

## Affiliate (Phase 8)

The `sportsbooks` collection starts EMPTY — no partnerships are invented.
Sportsbook CTA buttons on the frontend render ONLY when
`GET /api/affiliate/cta-status` reports `ctas_enabled: true` (server-side
gate). Admin endpoints require the `X-Admin-Key` header matching
`ADMIN_API_KEY` (501 when not configured, 403 on mismatch).

### `GET /api/affiliate/disclosure`

Disclosure text (env `AFFILIATE_DISCLOSURE_TEXT`, honest default otherwise).

```json
{ "disclosure": "Money By Numbers may receive compensation …", "configured": false }
```

### `GET /api/affiliate/cta-status`

```json
{ "ctas_enabled": false, "sportsbooks": [],
  "reason": "no sportsbook partnerships configured",
  "note": "No sportsbook partnerships are configured yet. …" }
```

When enabled: `{ "ctas_enabled": true, "sportsbooks": [{"sportsbook_id": "…", "name": "…"}] }`
— names only; affiliate URLs are never exposed to the client.

### `GET /r/{sportsbook_id}?cta_location=best_bets&game_id=…&market=…&side=…&edge=…&model_version=…&session_id=…`

Tracked referral. Logs the click to `affiliate_clicks` (reusing the Phase 1
click record: destination is the configured URL verbatim) and 302-redirects
to the sportsbook's configured affiliate URL. 404 when the id is unknown,
the partner is inactive, or no URL is configured — never a guessed URL.

### `GET /api/admin/sportsbooks` · `POST /api/admin/sportsbooks` · `PATCH /api/admin/sportsbooks/{id}` · `DELETE /api/admin/sportsbooks/{id}`

Partner CRUD. Create body: `{name, affiliate_url?, terms?, commission_structure?, active?}`
(`affiliate_url` must be http(s) or null; 422 otherwise). PATCH accepts the
same fields; setting `affiliate_url` to null disables CTAs. DELETE
deactivates (`active=false`) — click history is preserved. List responses
include `cta_enabled` per partner.

### `POST /api/admin/conversions` · `GET /api/admin/conversions?sportsbook_id=…`

Conversions transcribed from real affiliate reports:
`{sportsbook_id, click_id?, converted_at, revenue_amount?, currency?, notes?}`.
`revenue_amount` null = not reported (shown as unknown, never as zero).

### `GET /api/admin/affiliate/stats`

Aggregates from real logged data only. With no data, every section says so
honestly (`has_data: false` + note); zeros are labeled "no data yet", never
presented as performance.

---

## Phase 9 — Billing / subscriptions

Plans: **Free / Starter / Pro / Agency** (spec 53). Subscriptions are
secondary revenue (affiliate is primary); the free tier keeps the full
predictions/edges/track-record experience. Paid plans buy higher Numby
daily question limits (free: 20/day), CSV export of the settled pick
ledger, and bet alerts — all enforced server-side.

### `GET /api/billing/status`

Public. `stripe_configured` bool, the plan catalog (limits + features),
and prices fetched from Stripe at request time — or DATA UNAVAILABLE with
an honest note when Stripe/prices aren't configured. Also returns the
requester's own plan, read from the `subscriptions` collection, never from
client input. Secrets are never present in the response.

### `POST /api/billing/checkout` · `POST /api/billing/portal`

Checkout: `{plan}` → Stripe Checkout Session URL (401 anonymous, 501
until Stripe + the plan's price are configured, 422 for free/unknown
plans). The user key travels as `client_reference_id` + subscription
metadata so the webhook can link it back. Portal: Customer Portal URL for
the requester's Stripe customer (404 when none).

### `POST /api/billing/webhook`

Stripe event receiver (spec 54). The raw body is verified against
`STRIPE_WEBHOOK_SECRET` via the `Stripe-Signature` header (HMAC-SHA256,
5-minute tolerance); missing/invalid signatures → 400, unconfigured
secret → 501. Verified events apply idempotently (`stripe_events`
records each event id; redeliveries are acknowledged without re-applying).
Handled: `checkout.session.completed`, `customer.subscription.created/
updated/deleted`, `invoice.payment_failed`. This endpoint is the ONLY
writer of subscription plan/status — client input can never set them.

### `GET /api/billing/export/picks`

Starter+: CSV download of the settled pick ledger (header-only when the
ledger is empty). 401 anonymous, 402 free tier, 503 no database.

### `GET/POST/DELETE /api/billing/alerts`

Starter+: minimal bet-alert CRUD (`{name, team?, game_id?, edge_threshold?}`;
team validated as an NFL abbreviation). Users only ever see/delete their
own alerts.

### Plan gating elsewhere

`POST /api/numby/chat` enforces daily question quotas server-side when a
database is configured (anonymous traffic keyed by chat session, signed-in
users by their plan). Over-quota answers return `status: "quota_exceeded"`
with an upgrade nudge — never a silent failure. Without a database, chat
stays stateless and ungated (the honest fallback).

### Not yet built (explicitly out of scope for Phase 9)

- No Stripe keys, webhook secret, or price IDs are configured — billing is
  fully built but dormant until they are.
- No real subscriptions, payments, or revenue exist; nothing is fabricated.
- No raw card details are ever stored or touched.

---

## Not yet built

No auth (Google OAuth scaffold exists; user sessions not yet wired to
admin). They will be documented here as they are built.

## Scheduler observability (Phase 10, admin)

All endpoints require the `X-Admin-Key` header (501 when `ADMIN_API_KEY`
is unconfigured, 403 on mismatch) and a configured database (503
otherwise). Every value is read live from the scheduler's collections —
nothing is hardcoded.

- `GET /api/admin/scheduler/jobs` — one entry per scheduled job: cadence,
  in-season gating, lock state, `last_run_at`, `last_status`,
  `last_success_at`, `consecutive_failures`, computed `next_run_at`,
  a `missed` flag, and the 5 most recent runs.
- `GET /api/admin/scheduler/runs?job_name=&limit=` — the append-only run
  audit trail (start/end, status, duration, detail, error). Skips are
  recorded with their reason.
- `GET /api/admin/scheduler/alerts?job_name=&limit=` — persisted alerts:
  `job_failed`, `job_crashed` (stale lock reclaimed), `run_missed`.

### Not yet built (explicitly out of scope for Phase 10)

- No hosted-scheduler adapter (the cron/daemon runner is the production
  approach; a hosted adapter can be added without changing job code).
- Admin UI built (Phase 11 admin panel).
- No `ALERT_WEBHOOK_URL` configured — alerts persist to the database and
  logs until a webhook is set.

---

## Admin panel (Phase 11)

Frontend routes `/admin/login`, `/admin` (overview), `/admin/sportsbooks`,
`/admin/scheduler`, `/admin/subscriptions`, `/admin/models` — mobile-first,
desktop-capable, not linked from the public nav (URL-only). The admin key is
stored in **sessionStorage only** (tab-session lifetime, never localStorage);
the login page validates the key against `GET /api/admin/affiliate/stats`
before storing it. With `ADMIN_API_KEY` unset the backend answers 501 and
the UI says so honestly; a wrong key gets 403.

All admin API calls send the key as the `X-Admin-Key` header (constant-time
compare server-side). There is no default key and no bypass. The panel is a
control surface, not a data-fabrication surface: it cannot create, edit, or
delete settled picks (no write route to the `predictions` collection exists
anywhere in the API), cannot fabricate revenue, and cannot promote models —
model promotion is intentionally not a button; the registry page is read-only.

### `GET /api/admin/subscriptions/overview`

Plan/status aggregates from the real `subscriptions` collection:
`{total, has_data, by_plan, by_status, active_paid_subscriptions,
revenue: null, note}`. Revenue is always `null` here — it lives with Stripe
and is never estimated. Empty state: `has_data: false` with an explanatory
note.

---

## Phase 12 — security hardening (built 2026-09-09, spec 62-63)

### HTTP security headers (all responses)

`backend/app/middleware.py::SecurityHeadersMiddleware` attaches to every
response, including 429s and CORS rejections:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`
- `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — only
  when `ENVIRONMENT=production` or the request arrived over HTTPS.

### CORS

Explicit origins only, from `CORS_ORIGINS` (comma-separated; default
`http://localhost:3000`). **The app refuses to boot** when
`ENVIRONMENT=production` and the list contains `*` — production must name
its frontend domains.

### Rate limits (per-IP, in-memory sliding window)

| Prefix            | Env var (per min, default)     | On exceed |
|-------------------|-------------------------------|-----------|
| `/api/numby/chat` | `RATE_LIMIT_NUMBY_PER_MIN` (30) | 429 `rate_limited` + `Retry-After` |
| `/r/`             | `RATE_LIMIT_REDIRECT_PER_MIN` (60) | 429 `rate_limited` + `Retry-After` |

CORS preflight (`OPTIONS`) never consumes budget. Limitation: state is
per-process, so multi-worker deployments should add a gateway/WAF limit
for hard guarantees (noted in `docs/deployment.md`).

### Other Phase 12 findings (honest)

- Dependency scan (OSV, 2026-09-09): all Python app dependencies clean;
  all 59 locked npm packages clean **after** bumping `postcss`
  8.4.31 → 8.5.28 (4 advisories fixed, incl. arbitrary file read via
  sourceMappingURL). `requests` 2.31.0 shows 6 advisories in the local
  sandbox env but is **not** an app dependency (not in
  `requirements.txt`, never imported) — informational only.
- Secret scan: no `.env` committed, `.env.example` holds empty
  placeholders only, test keys are obvious placeholders, no committed
  private keys, no credentials in logs. (No git history exists to audit.)
- No PII/credential/card-data logging found in routers.
