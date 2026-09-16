// Admin API client for the Money By Numbers admin panel (Phase 11).
//
// The admin key is stored in sessionStorage only — it disappears when the
// tab closes and is never persisted to localStorage, cookies, or disk by
// this client. Every request sends it as the X-Admin-Key header, which the
// backend compares (constant-time) against ADMIN_API_KEY. The backend has
// no default key: with ADMIN_API_KEY unset it answers 501, and this client
// surfaces that honestly.

import { API_URL } from "./config";

const KEY_STORAGE = "mbn_admin_key";

export function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setAdminKey(key: string): void {
  window.sessionStorage.setItem(KEY_STORAGE, key);
}

export function clearAdminKey(): void {
  try {
    window.sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* ignore */
  }
}

export type AdminApiError =
  | { kind: "unconfigured"; message: string } // 501 — ADMIN_API_KEY unset
  | { kind: "forbidden"; message: string } // 403 — wrong key
  | { kind: "unreachable"; message: string } // network failure
  | { kind: "error"; status: number; message: string };

export async function adminFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T | null; error: AdminApiError | null }> {
  const key = getAdminKey();
  if (!key) {
    return {
      data: null,
      error: { kind: "forbidden", message: "No admin key in this session." },
    };
  }
  try {
    const res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        "X-Admin-Key": key,
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      cache: "no-store",
    });
    if (res.status === 501) {
      const body = await res.json().catch(() => ({}));
      return {
        data: null,
        error: {
          kind: "unconfigured",
          message:
            (body as { detail?: string }).detail ??
            "Admin API is not configured on the server (ADMIN_API_KEY unset).",
        },
      };
    }
    if (res.status === 403) {
      return {
        data: null,
        error: { kind: "forbidden", message: "Invalid admin key." },
      };
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return {
        data: null,
        error: {
          kind: "error",
          status: res.status,
          message:
            (body as { detail?: string }).detail ??
            `Request failed with status ${res.status}.`,
        },
      };
    }
    return { data: (await res.json()) as T, error: null };
  } catch {
    return {
      data: null,
      error: { kind: "unreachable", message: "Backend unreachable." },
    };
  }
}

// ---------------------------------------------------------------------------
// Typed shapes — mirror the real backend responses; nothing is fabricated.
// ---------------------------------------------------------------------------

export interface Sportsbook {
  sportsbook_id: string;
  name: string;
  affiliate_url: string | null;
  terms?: string | null;
  commission_structure?: string | null;
  active: boolean;
  cta_enabled: boolean;
}

export interface SchedulerJob {
  name: string;
  schedule?: string | null;
  enabled?: boolean;
  last_run?: Record<string, unknown> | null;
  next_run_at?: string | null;
  consecutive_failures?: number;
  missed?: boolean;
  [key: string]: unknown;
}

export interface AffiliateStats {
  clicks: { total: number; has_data: boolean; note: string | null };
  conversions: {
    total: number;
    has_data: boolean;
    conversion_rate: number | null;
    note: string | null;
  };
  revenue: {
    total: number | null;
    currency: string;
    reported_conversions: number;
    has_data: boolean;
    note: string | null;
  };
  by_sportsbook: Array<{
    sportsbook_id: string;
    name: string;
    active: boolean;
    cta_enabled: boolean;
    clicks: number;
    conversions: number;
    revenue: number | null;
  }>;
}

export interface SubscriptionOverview {
  total: number;
  has_data: boolean;
  by_plan: Record<string, number>;
  by_status: Record<string, number>;
  active_paid_subscriptions: number;
  revenue: null;
  note: string | null;
}

export interface RegisteredModel {
  model_version: string;
  champion?: boolean;
  registered_at?: string;
  training_period?: string;
  metrics?: Record<string, unknown>;
  [key: string]: unknown;
}

export function fetchSportsbooks() {
  return adminFetch<{ sportsbooks: Sportsbook[]; note?: string }>(
    "/api/admin/sportsbooks",
  );
}

export function createSportsbook(body: {
  name: string;
  affiliate_url?: string | null;
  terms?: string | null;
  commission_structure?: string | null;
  active?: boolean;
}) {
  return adminFetch<{ status: string; sportsbook: Sportsbook }>(
    "/api/admin/sportsbooks",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function updateSportsbook(
  id: string,
  body: Partial<{
    name: string;
    affiliate_url: string | null;
    terms: string | null;
    commission_structure: string | null;
    active: boolean;
  }>,
) {
  return adminFetch<{ status: string; sportsbook: Sportsbook }>(
    `/api/admin/sportsbooks/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function deactivateSportsbook(id: string) {
  return adminFetch<{ status: string; sportsbook_id: string }>(
    `/api/admin/sportsbooks/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function fetchSchedulerJobs() {
  return adminFetch<{ jobs: SchedulerJob[] }>("/api/admin/scheduler/jobs");
}

export function fetchSchedulerRuns(jobName?: string, limit = 25) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (jobName) q.set("job_name", jobName);
  return adminFetch<{ runs: Record<string, unknown>[]; count: number }>(
    `/api/admin/scheduler/runs?${q}`,
  );
}

export function fetchSchedulerAlerts(limit = 25) {
  return adminFetch<{ alerts: Record<string, unknown>[]; count: number }>(
    `/api/admin/scheduler/alerts?limit=${limit}`,
  );
}

export function fetchAffiliateStats() {
  return adminFetch<AffiliateStats>("/api/admin/affiliate/stats");
}

export function fetchSubscriptionOverview() {
  return adminFetch<SubscriptionOverview>("/api/admin/subscriptions/overview");
}

/** Model registry is a public read endpoint; admin views it read-only. */
export async function fetchModelRegistry(): Promise<{
  status: string;
  models: RegisteredModel[];
} | null> {
  try {
    const res = await fetch(`${API_URL}/api/predictions/models`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as { status: string; models: RegisteredModel[] };
  } catch {
    return null;
  }
}
