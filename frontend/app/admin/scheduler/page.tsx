"use client";

import { useEffect, useState } from "react";
import { AdminShell } from "@/components/AdminShell";
import {
  fetchSchedulerAlerts,
  fetchSchedulerJobs,
  fetchSchedulerRuns,
  type AdminApiError,
  type SchedulerJob,
} from "@/lib/admin";

/**
 * Scheduler observability (Phase 10 admin endpoints): job list with
 * last/next run and failure flags, recent run records, and alerts.
 * Read-only — jobs are run by the scheduler process, not from this page.
 */

function fmtTime(v: unknown): string {
  if (typeof v !== "string" || !v) return "—";
  try {
    return new Date(v).toLocaleString();
  } catch {
    return v;
  }
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function AdminSchedulerPage() {
  const [jobs, setJobs] = useState<SchedulerJob[] | null>(null);
  const [jobsError, setJobsError] = useState<AdminApiError | null>(null);
  const [runs, setRuns] = useState<Record<string, unknown>[] | null>(null);
  const [alerts, setAlerts] = useState<Record<string, unknown>[] | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetchSchedulerJobs().then((r) => {
      setJobs(r.data?.jobs ?? null);
      setJobsError(r.error);
    });
    fetchSchedulerRuns(filter || undefined).then((r) => setRuns(r.data?.runs ?? null));
    fetchSchedulerAlerts().then((r) => setAlerts(r.data?.alerts ?? null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function applyFilter() {
    const r = await fetchSchedulerRuns(filter || undefined);
    setRuns(r.data?.runs ?? null);
  }

  return (
    <AdminShell>
      <h2 className="section-title" style={{ fontSize: "1.2rem" }}>
        Scheduled jobs
      </h2>
      {jobsError && (
        <div className="empty-state" style={{ textAlign: "left" }} role="alert">
          <strong>Could not load jobs:</strong> {jobsError.message}
        </div>
      )}
      {jobs && jobs.length === 0 && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          No scheduler jobs registered yet.
        </p>
      )}
      {jobs && jobs.length > 0 && (
        <table className="mnb-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Last run</th>
              <th>Next run</th>
              <th>Failures</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={String(j.name)}>
                <td>
                  <strong>{String(j.name)}</strong>
                  <br />
                  <span style={{ fontSize: "0.8rem" }}>
                    {fmtCell(j.schedule)}
                  </span>
                </td>
                <td>{fmtTime((j.last_run as Record<string, unknown> | null)?.started_at ?? j.last_run)}</td>
                <td>{fmtTime(j.next_run_at)}</td>
                <td>{fmtCell(j.consecutive_failures ?? 0)}</td>
                <td>
                  <span className="badge">
                    {j.missed
                      ? "missed"
                      : (j.consecutive_failures ?? 0) > 0
                        ? "failing"
                        : j.enabled === false
                          ? "disabled"
                          : "ok"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 32 }}>Recent runs</h3>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by job name (optional)"
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid var(--border, #333)",
            background: "var(--bg-raised, #111)",
            color: "inherit",
            fontSize: "0.9rem",
          }}
        />
        <button className="btn btn-ghost" onClick={applyFilter}>
          Apply
        </button>
      </div>
      {runs && runs.length === 0 && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          No run records yet.
        </p>
      )}
      {runs && runs.length > 0 && (
        <table className="mnb-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Started</th>
              <th>Status</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={i}>
                <td>{fmtCell(r.job_name)}</td>
                <td>{fmtTime(r.started_at)}</td>
                <td>
                  <span className="badge">{fmtCell(r.status)}</span>
                </td>
                <td style={{ fontSize: "0.85rem" }}>
                  {fmtCell(r.error ?? r.summary ?? "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 32 }}>Alerts</h3>
      {alerts && alerts.length === 0 && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          No alerts. Job failures, crashed runs, and missed runs would appear
          here.
        </p>
      )}
      {alerts && alerts.length > 0 && (
        <table className="mnb-table">
          <thead>
            <tr>
              <th>Created</th>
              <th>Job</th>
              <th>Alert</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a, i) => (
              <tr key={i}>
                <td>{fmtTime(a.created_at)}</td>
                <td>{fmtCell(a.job_name)}</td>
                <td>
                  <span className="badge">{fmtCell(a.alert_type)}</span>{" "}
                  <span style={{ fontSize: "0.85rem" }}>
                    {fmtCell(a.message ?? a.detail ?? "")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </AdminShell>
  );
}
