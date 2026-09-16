"use client";

import { useEffect, useState } from "react";
import { AdminShell } from "@/components/AdminShell";
import {
  fetchSubscriptionOverview,
  type AdminApiError,
  type SubscriptionOverview,
} from "@/lib/admin";

/**
 * Subscription overview — read-only aggregates from the subscriptions
 * collection. Revenue is never estimated here; it lives with Stripe.
 */
export default function AdminSubscriptionsPage() {
  const [subs, setSubs] = useState<SubscriptionOverview | null>(null);
  const [error, setError] = useState<AdminApiError | null>(null);

  useEffect(() => {
    fetchSubscriptionOverview().then((r) => {
      setSubs(r.data);
      setError(r.error);
    });
  }, []);

  return (
    <AdminShell>
      <h2 className="section-title" style={{ fontSize: "1.2rem" }}>
        Subscriptions
      </h2>
      {error && (
        <div className="empty-state" style={{ textAlign: "left" }} role="alert">
          <strong>Could not load:</strong> {error.message}
        </div>
      )}
      {subs && !subs.has_data && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          {subs.note ?? "No subscription records yet."}
        </p>
      )}
      {subs && subs.has_data && (
        <>
          <div className="card-grid">
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Total records</h3>
              <p style={{ fontSize: "1.6rem", margin: 0 }}>{subs.total}</p>
            </div>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Active paid</h3>
              <p style={{ fontSize: "1.6rem", margin: 0 }}>
                {subs.active_paid_subscriptions}
              </p>
            </div>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Revenue</h3>
              <p style={{ margin: 0 }}>Unknown — reported by Stripe, never estimated.</p>
            </div>
          </div>
          <h3 style={{ marginTop: 24 }}>By plan</h3>
          <table className="mnb-table">
            <thead>
              <tr>
                <th>Plan</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(subs.by_plan).map(([plan, n]) => (
                <tr key={plan}>
                  <td>{plan}</td>
                  <td>{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ marginTop: 24 }}>By status</h3>
          <table className="mnb-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(subs.by_status).map(([status, n]) => (
                <tr key={status}>
                  <td>{status}</td>
                  <td>{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="context-note" style={{ marginTop: 24 }}>
        Subscription status is written only by the Stripe webhook from
        verified events — this panel cannot change anyone&apos;s plan.
      </p>
    </AdminShell>
  );
}
