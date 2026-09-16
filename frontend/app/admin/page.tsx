"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AdminShell } from "@/components/AdminShell";
import {
  fetchAffiliateStats,
  fetchModelRegistry,
  fetchSchedulerAlerts,
  fetchSchedulerJobs,
  fetchSubscriptionOverview,
  type AffiliateStats,
  type AdminApiError,
  type RegisteredModel,
  type SchedulerJob,
  type SubscriptionOverview,
} from "@/lib/admin";
import { fetchDataHealth, fetchApiHealth, type ApiHealth, type DataHealth } from "@/lib/api";

/**
 * Admin overview — status cards pulled live from real endpoints:
 * data health, model registry, odds/data status, scheduler, affiliate,
 * subscriptions. Every card has an honest empty state; nothing here is
 * fabricated. This page is read-only.
 */

function Card({
  title,
  href,
  children,
}: {
  title: string;
  href?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
      {href && (
        <p style={{ marginBottom: 0 }}>
          <Link href={href}>Open →</Link>
        </p>
      )}
    </div>
  );
}

function ErrorNote({ error }: { error: AdminApiError | null }) {
  if (!error) return null;
  const label =
    error.kind === "unconfigured"
      ? "Not configured"
      : error.kind === "forbidden"
        ? "Access denied"
        : error.kind === "unreachable"
          ? "Unreachable"
          : "Error";
  return (
    <p className="empty-state" style={{ textAlign: "left", margin: "8px 0" }}>
      <strong>{label}:</strong> {error.message}
    </p>
  );
}

export default function AdminDashboardPage() {
  const [apiHealth, setApiHealth] = useState<ApiHealth | null>(null);
  const [dataHealth, setDataHealth] = useState<DataHealth | null>(null);
  const [models, setModels] = useState<{ status: string; models: RegisteredModel[] } | null>(null);
  const [jobs, setJobs] = useState<SchedulerJob[] | null>(null);
  const [jobsError, setJobsError] = useState<AdminApiError | null>(null);
  const [alerts, setAlerts] = useState<number | null>(null);
  const [aff, setAff] = useState<AffiliateStats | null>(null);
  const [affError, setAffError] = useState<AdminApiError | null>(null);
  const [subs, setSubs] = useState<SubscriptionOverview | null>(null);
  const [subsError, setSubsError] = useState<AdminApiError | null>(null);

  useEffect(() => {
    fetchApiHealth().then(setApiHealth);
    fetchDataHealth().then(setDataHealth);
    fetchModelRegistry().then(setModels);
    fetchSchedulerJobs().then((r) => {
      setJobs(r.data?.jobs ?? null);
      setJobsError(r.error);
    });
    fetchSchedulerAlerts(50).then((r) => setAlerts(r.data?.count ?? null));
    fetchAffiliateStats().then((r) => {
      setAff(r.data);
      setAffError(r.error);
    });
    fetchSubscriptionOverview().then((r) => {
      setSubs(r.data);
      setSubsError(r.error);
    });
  }, []);

  const failedJobs = (jobs ?? []).filter(
    (j) => (j.consecutive_failures ?? 0) > 0 || j.missed,
  );
  const champion = (models?.models ?? []).find((m) => m.champion);

  return (
    <AdminShell>
      <div className="card-grid">
        <Card title="Data health">
          {dataHealth ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {Object.entries(dataHealth).map(([k, v]) =>
                typeof v === "object" && v !== null && "status" in v ? (
                  <li key={k}>
                    {k}: {(v as { status: string }).status}
                  </li>
                ) : null,
              )}
            </ul>
          ) : (
            <p className="empty-state" style={{ textAlign: "left" }}>
              DATA UNAVAILABLE — backend unreachable or no database.
            </p>
          )}
          {apiHealth && (
            <p style={{ fontSize: "0.85rem" }}>API: {apiHealth.status}</p>
          )}
        </Card>

        <Card title="Models" href="/admin/models">
          {models ? (
            models.models.length > 0 ? (
              <p>
                {models.models.length} registered version
                {models.models.length === 1 ? "" : "s"}
                {champion
                  ? ` · champion: ${champion.model_version}`
                  : " · no champion flagged"}
              </p>
            ) : (
              <p className="empty-state" style={{ textAlign: "left" }}>
                No model versions registered yet.
              </p>
            )
          ) : (
            <p className="empty-state" style={{ textAlign: "left" }}>
              DATA UNAVAILABLE — model registry unreachable.
            </p>
          )}
          <p style={{ fontSize: "0.85rem" }}>
            Read-only. Model promotion is not exposed in this panel.
          </p>
        </Card>

        <Card title="Scheduler" href="/admin/scheduler">
          <ErrorNote error={jobsError} />
          {jobs ? (
            <p>
              {jobs.length} job{jobs.length === 1 ? "" : "s"} ·{" "}
              {failedJobs.length} with failures or missed runs
            </p>
          ) : (
            !jobsError && (
              <p className="empty-state" style={{ textAlign: "left" }}>
                Loading…
              </p>
            )
          )}
          {alerts !== null && alerts > 0 && (
            <p>
              <strong>{alerts}</strong> recent alert{alerts === 1 ? "" : "s"}
            </p>
          )}
        </Card>

        <Card title="Affiliate" href="/admin/sportsbooks">
          <ErrorNote error={affError} />
          {aff ? (
            <>
              <p>
                {aff.by_sportsbook.length} partner
                {aff.by_sportsbook.length === 1 ? "" : "s"} ·{" "}
                {aff.clicks.total} click{aff.clicks.total === 1 ? "" : "s"} ·{" "}
                {aff.conversions.total} conversion
                {aff.conversions.total === 1 ? "" : "s"}
              </p>
              <p style={{ fontSize: "0.85rem" }}>
                {aff.revenue.has_data
                  ? `Reported revenue: ${aff.revenue.currency} ${aff.revenue.total}`
                  : "Revenue: unknown — not reported yet, never estimated."}
              </p>
            </>
          ) : (
            !affError && (
              <p className="empty-state" style={{ textAlign: "left" }}>
                Loading…
              </p>
            )
          )}
        </Card>

        <Card title="Subscriptions" href="/admin/subscriptions">
          <ErrorNote error={subsError} />
          {subs ? (
            subs.has_data ? (
              <p>
                {subs.total} record{subs.total === 1 ? "" : "s"} ·{" "}
                {subs.active_paid_subscriptions} active paid
              </p>
            ) : (
              <p className="empty-state" style={{ textAlign: "left" }}>
                {subs.note ?? "No subscription records yet."}
              </p>
            )
          ) : (
            !subsError && (
              <p className="empty-state" style={{ textAlign: "left" }}>
                Loading…
              </p>
            )
          )}
        </Card>
      </div>
      <p className="context-note" style={{ marginTop: 24 }}>
        The admin panel is a control surface, not a data-fabrication surface.
        It cannot create, edit, or delete settled picks, fabricate revenue, or
        silently change the production model.
      </p>
    </AdminShell>
  );
}
