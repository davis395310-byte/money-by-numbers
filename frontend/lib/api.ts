// Typed API client for the Money By Numbers backend.
// All fetchers tolerate a missing or unreachable backend: callers get a
// `null` result and render an honest "DATA UNAVAILABLE" empty state.

import { API_URL } from "./config";

export interface HealthCheck {
  status: string;
  detail?: string;
}

export interface ApiHealth {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, HealthCheck>;
}

export interface DataSourceStatus {
  status: string;
  last_ingestion: string | null;
  record_count: number;
  freshness: string | null;
}

export interface DataHealth {
  games: DataSourceStatus;
  injuries: DataSourceStatus;
  odds: DataSourceStatus;
  weather: DataSourceStatus;
  errors: string[];
}

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function fetchApiHealth(): Promise<ApiHealth | null> {
  return get<ApiHealth>("/api/health");
}

export function fetchDataHealth(): Promise<DataHealth | null> {
  return get<DataHealth>("/api/data/health");
}

// ---------------------------------------------------------------------------
// Edges + odds (Phase 4). Shapes below mirror the real backend
// (backend/app/routers/odds.py), which builds rows with
// backend/app/edges/engine.py. All fields come from the API — this file
// never fabricates numbers.
// ---------------------------------------------------------------------------

/** Confidence tier assigned by the edge engine: HIGH / MEDIUM / LOW. */
export type ConfidenceTier = "HIGH" | "MEDIUM" | "LOW";

/** One ranked model edge from GET /api/edges. */
export interface Edge {
  game_id: string;
  /** Market the edge applies to: moneyline | spread | total. */
  market: string;
  /** Side of the market: "home" | "away" | "over" | "under". */
  side: string;
  /** Point line for spread/total markets; null for moneyline. */
  line: number | null;
  /** Market price for this side in American odds (null if not quoted). */
  price: number | null;
  /** Model probability for this side. */
  model_probability: number;
  /** No-vig implied probability for this side at the market price. */
  novig_implied: number;
  /** Fractional edge: model_probability - novig_implied. */
  edge: number;
  /** Expected value per unit staked (null when price is missing). */
  ev: number | null;
  confidence: ConfidenceTier;
  /** Model version that produced the prediction. */
  model_version: string;
}

export type EdgesStatus = "ok" | "no_edges" | "edges_unavailable" | "error";

export interface EdgesResponse {
  status: EdgesStatus;
  /** Human-readable reason when status is not "ok". */
  reason?: string;
  /** Model version whose predictions were joined with market odds. */
  model_version?: string;
  /** Edge threshold used for this response (default 0.03). */
  threshold?: number;
  /** Newest odds-snapshot timestamp backing these edges (ISO). Local
      stored-edges fallback uses it for the as-of label. */
  snapshot_as_of?: string;
  /** True when the backing snapshot is older than one refresh cycle. */
  snapshot_stale?: boolean;
  /** Ranked edges (empty unless status is "ok"). */
  edges: Edge[];
}

export type OddsStatusValue = "odds_available" | "odds_unavailable";

export interface OddsStatusResponse {
  status: OddsStatusValue;
  /** Human-readable reason when status is not "odds_available". */
  reason?: string;
  /** Whether an odds API key is configured on the backend. */
  key_configured?: boolean;
  /** Remaining odds-API credits (null when unknown). */
  credits_remaining?: number | null;
  snapshot_count?: number;
}

export function fetchEdges(): Promise<EdgesResponse | null> {
  return get<EdgesResponse>("/api/edges");
}

export function fetchOddsStatus(): Promise<OddsStatusResponse | null> {
  return get<OddsStatusResponse>("/api/odds/status");
}

// ---------------------------------------------------------------------------
// Game context: injuries + weather (Phase 5). Shapes mirror the real backend
// (backend/app/routers/game_context.py). Statuses drive honest empty states:
// "ok" | "unavailable" | "no_games" | "no_context" | "error".
// ---------------------------------------------------------------------------

/** One game from GET /api/games. */
export interface Game {
  game_id: string;
  season: number;
  week: number;
  game_date: string | null;
  home_team: string;
  away_team: string;
  venue: string | null;
  roof: string | null;
  status: string | null;
}

/** Games list envelope from GET /api/games. */
export interface GamesList {
  status: "ok" | "no_games" | "error";
  reason?: string;
  games: Game[];
}

/** One injury report row from GET /api/games/{game_id}/context. */
export interface InjuryRecord {
  season: number;
  week: number;
  team: string;
  position: string | null;
  full_name: string;
  report_status: string | null;
  report_primary_injury: string | null;
  practice_status: string | null;
  date_modified: string | null;
}

export interface InjurySection {
  status: "ok" | "unavailable" | "error";
  reason?: string;
  records: InjuryRecord[];
  summary?: Record<string, number>;
  note?: string;
}

/** One kickoff-hour weather row. */
export interface WeatherRecord {
  game_id: string;
  venue: string | null;
  kickoff: string | null;
  temp_f: number | null;
  wind_mph: number | null;
  wind_gust_mph: number | null;
  precip_prob: number | null;
  precip_in: number | null;
  conditions: string | null;
  dome: boolean | null;
  source: string | null;
}

export interface WeatherSection {
  status: "ok" | "unavailable" | "error";
  reason?: string;
  record?: WeatherRecord;
}

/** Per-game context envelope from GET /api/games/{game_id}/context. */
export interface GameContext {
  status: "ok" | "no_context" | "error";
  reason?: string;
  game_id: string;
  game?: Game;
  injuries: InjurySection;
  weather: WeatherSection;
}

export function fetchGames(
  season?: number,
  week?: number,
): Promise<GamesList | null> {
  const params = new URLSearchParams();
  if (season !== undefined) params.set("season", String(season));
  if (week !== undefined) params.set("week", String(week));
  const query = params.toString();
  return get<GamesList>(`/api/games${query ? `?${query}` : ""}`);
}

export function fetchGameContext(gameId: string): Promise<GameContext | null> {
  return get<GameContext>(`/api/games/${encodeURIComponent(gameId)}/context`);
}

// ---------------------------------------------------------------------------
// Track record + methodology (Phase 6). Shapes mirror the real backend
// (backend/app/routers/track_record.py). Every number is computed from the
// immutable pick ledger at request time — this file never fabricates them.
// ---------------------------------------------------------------------------

/** Win/loss/push aggregate computed from settled picks. */
export interface RecordSummary {
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  ungraded: number;
  accuracy: number | null;
  roi_units: number | null;
  roi_n: number;
  roi_excluded_no_price: number;
  avg_brier: number | null;
  brier_n: number;
}

export type TrackRecordStatus = "ok" | "no_settled_picks" | "no_data" | "error";

export interface TrackRecordSummaryResponse {
  status: TrackRecordStatus;
  reason?: string;
  summary: RecordSummary | null;
  by_market?: Record<string, RecordSummary>;
  pending_picks?: number;
  note?: string;
}

export interface TrackRecordTiersResponse {
  status: TrackRecordStatus;
  reason?: string;
  tiers: Record<"HIGH" | "MEDIUM" | "LOW", RecordSummary> | null;
}

export interface CalibrationBucket {
  prob_low: number;
  prob_high: number;
  n: number;
  avg_predicted: number;
  observed_win_rate: number;
}

export interface TrackRecordCalibrationResponse {
  status: TrackRecordStatus;
  reason?: string;
  buckets: CalibrationBucket[] | null;
}

/** One locked pick joined with its settlement (if settled). */
export interface TrackedPick {
  prediction_id: string;
  game_id: string;
  season: number | null;
  week: number | null;
  home_team: string | null;
  away_team: string | null;
  model_version: string;
  model_probability: number;
  predicted_winner: string;
  market: string | null;
  side: string | null;
  market_line_at_pick: number | null;
  market_price_at_pick: number | null;
  confidence: string;
  created_at: string;
  settlement_status: "pending" | "settled";
  result?: "win" | "loss" | "push" | "ungraded";
  actual_home_score?: number | null;
  actual_away_score?: number | null;
  clv_favorable?: boolean | null;
}

export interface TrackRecordPicksResponse {
  status: "ok" | "no_picks" | "no_data" | "error";
  reason?: string;
  picks: TrackedPick[];
}

/** Per-season walk-forward row, read from the real training artifacts. */
export interface WalkForwardSeason {
  season: number;
  n_games: number | null;
  accuracy: number | null;
  brier: number | null;
  elo_accuracy: number | null;
}

export interface WalkForwardResults {
  model_version: string;
  training_period: string;
  training_date: string;
  n_games: number;
  seasons: number[];
  ensemble_accuracy: number;
  ensemble_brier: number;
  elo_baseline_accuracy: number;
  elo_baseline_brier: number;
  accuracy_vs_elo: number;
  ats_accuracy: number;
  ats_n: number;
  margin_mae: number;
  note: string;
  per_season: WalkForwardSeason[];
  leakage_harness: string;
}

export interface MethodologyResponse {
  status: "ok" | "no_methodology" | "error";
  reason?: string;
  walk_forward: WalkForwardResults | null;
  research_note: string | null;
  research_note_status?: string;
  limits?: string[];
}

export function fetchTrackRecordSummary(): Promise<TrackRecordSummaryResponse | null> {
  return get<TrackRecordSummaryResponse>("/api/track-record/summary");
}

export function fetchTrackRecordTiers(): Promise<TrackRecordTiersResponse | null> {
  return get<TrackRecordTiersResponse>("/api/track-record/tiers");
}

export function fetchTrackRecordCalibration(): Promise<TrackRecordCalibrationResponse | null> {
  return get<TrackRecordCalibrationResponse>("/api/track-record/calibration");
}

export function fetchTrackRecordPicks(result?: string): Promise<TrackRecordPicksResponse | null> {
  const query = result ? `?result=${encodeURIComponent(result)}` : "";
  return get<TrackRecordPicksResponse>(`/api/track-record/picks${query}`);
}

export function fetchMethodology(): Promise<MethodologyResponse | null> {
  return get<MethodologyResponse>("/api/methodology");
}

// ---------------------------------------------------------------------------
// Numby chat (Phase 7). Shapes mirror backend/app/routers/numby.py.
// The backend answers only from stored platform data; this file never
// fabricates message content.
// ---------------------------------------------------------------------------

/** One chat message. */
export interface NumbyMessage {
  role: "user" | "assistant";
  content: string;
}

/** Answer mode reported by the backend. */
export type NumbyAiMode = "basic" | "basic_fallback" | "llm" | "guardrail";

export interface NumbyChatResponse {
  status: "ok" | "error" | "quota_exceeded";
  reason?: string;
  reply: string | null;
  /** Present when status is "quota_exceeded" (Phase 9 plan gating). */
  plan?: string;
  upgrade_url?: string;
  /** "kind:source" labels for the facts the answer was grounded in. */
  sources: string[];
  ai_mode: NumbyAiMode;
  /** Responsible-gambling disclosure, present on betting-related answers. */
  disclaimer: string | null;
  session_id: string | null;
  persistent: boolean;
}

export interface NumbyHistoryResponse {
  status: "ok" | "no_history" | "error";
  reason?: string;
  session_id?: string;
  messages: NumbyMessage[];
}

export interface NumbyStatusResponse {
  status: "ok" | "error";
  ai_configured: boolean;
  provider: string;
  mode: "llm" | "basic";
  note: string;
}

async function post<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function sendNumbyMessage(
  message: string,
  sessionId: string | null,
): Promise<NumbyChatResponse | null> {
  return post<NumbyChatResponse>("/api/numby/chat", {
    message,
    session_id: sessionId,
  });
}

export function fetchNumbyHistory(
  sessionId: string,
): Promise<NumbyHistoryResponse | null> {
  return get<NumbyHistoryResponse>(
    `/api/numby/history?session_id=${encodeURIComponent(sessionId)}`,
  );
}

export function fetchNumbyStatus(): Promise<NumbyStatusResponse | null> {
  return get<NumbyStatusResponse>("/api/numby/status");
}

// ---------------------------------------------------------------------------
// Affiliate (Phase 8). Shapes mirror the real backend
// (backend/app/routers/affiliate.py). CTAs render ONLY when
// GET /api/affiliate/cta-status reports ctas_enabled: true — the gate is
// server-side. Affiliate URLs are never exposed to the client; the CTA
// links hit GET /r/{sportsbook_id}, which logs the click and 302-redirects.
// ---------------------------------------------------------------------------

/** One CTA-eligible sportsbook from GET /api/affiliate/cta-status. */
export interface CtaSportsbook {
  sportsbook_id: string;
  name: string;
}

export interface CtaStatusResponse {
  ctas_enabled: boolean;
  sportsbooks: CtaSportsbook[];
  reason?: string;
  note?: string;
}

export interface DisclosureResponse {
  disclosure: string;
  configured: boolean;
}

export function fetchCtaStatus(): Promise<CtaStatusResponse | null> {
  return get<CtaStatusResponse>("/api/affiliate/cta-status");
}

export function fetchDisclosure(): Promise<DisclosureResponse | null> {
  return get<DisclosureResponse>("/api/affiliate/disclosure");
}

/** Tracked-referral URL for a sportsbook CTA (backend logs + 302s). */
export function referralUrl(sportsbookId: string): string {
  return `${API_URL}/r/${encodeURIComponent(sportsbookId)}`;
}

// ---------------------------------------------------------------------------
// Billing / subscriptions (Phase 9). Shapes mirror the real backend
// (backend/app/routers/billing.py). Prices come from Stripe via the API or
// are DATA UNAVAILABLE — this file never fabricates prices, plans, or
// subscription state.
// ---------------------------------------------------------------------------

export interface PlanPrice {
  amount_cents: number | null;
  currency: string | null;
  interval: string | null;
  note: string | null;
}

export interface BillingPlan {
  plan: string;
  display_name: string;
  tagline: string;
  features: string[];
  limits: {
    numby_questions_per_day: number;
    csv_export: boolean;
    bet_alerts: boolean;
    seats: number;
  };
  price_configured: boolean;
  price: PlanPrice;
}

export interface BillingStatus {
  status: string;
  stripe_configured: boolean;
  plans: BillingPlan[];
  current_user: {
    signed_in: boolean;
    plan: string;
    subscription: {
      plan: string;
      status: string;
      current_period_end: string | null;
      cancel_at_period_end: boolean;
    } | null;
  };
  note: string | null;
}

export interface CheckoutResponse {
  status: string;
  checkout_url: string;
  session_id: string;
}

export interface PortalResponse {
  status: string;
  portal_url: string;
}

export interface BetAlert {
  alert_id: string;
  user_id: string;
  name: string;
  team: string | null;
  game_id: string | null;
  edge_threshold: number | null;
  active: boolean;
  created_at: string;
}

export function fetchBillingStatus(): Promise<BillingStatus | null> {
  return get<BillingStatus>("/api/billing/status");
}

/** POST that surfaces the HTTP status so the page can explain 401/501. */
async function postWithStatus<T>(
  path: string,
  body: unknown,
): Promise<{ ok: boolean; status: number; data: T | null }> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return { ok: false, status: res.status, data: null };
    return { ok: true, status: res.status, data: (await res.json()) as T };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

export function startCheckout(
  plan: string,
): Promise<{ ok: boolean; status: number; data: CheckoutResponse | null }> {
  return postWithStatus<CheckoutResponse>("/api/billing/checkout", { plan });
}

export function openCustomerPortal(): Promise<{
  ok: boolean;
  status: number;
  data: PortalResponse | null;
}> {
  return postWithStatus<PortalResponse>("/api/billing/portal", {});
}

export function fetchAlerts(): Promise<{ alerts: BetAlert[]; note?: string } | null> {
  return get<{ alerts: BetAlert[]; note?: string }>("/api/billing/alerts");
}

/** Format a Stripe price honestly; nulls become DATA UNAVAILABLE. */
export function formatPrice(price: PlanPrice): string {
  if (price.amount_cents === null || price.amount_cents === undefined) {
    return "DATA UNAVAILABLE";
  }
  const dollars = (price.amount_cents / 100).toFixed(2).replace(/\.00$/, "");
  const interval = price.interval ? `/${price.interval}` : "";
  return `$${dollars} ${price.currency ?? ""}${interval}`.trim();
}
