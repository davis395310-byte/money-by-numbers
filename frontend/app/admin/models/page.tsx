"use client";

import { useEffect, useState } from "react";
import { AdminShell } from "@/components/AdminShell";
import {
  fetchModelRegistry,
  type RegisteredModel,
} from "@/lib/admin";

/**
 * Model registry — READ ONLY. This panel lists registered model versions
 * and which is champion. There is no promotion control here: a production
 * model is never silently replaced, and promotion requires an explicit,
 * audited operation outside this panel (not yet exposed).
 */
export default function AdminModelsPage() {
  const [status, setStatus] = useState<string | null>(null);
  const [models, setModels] = useState<RegisteredModel[] | null>(null);

  useEffect(() => {
    fetchModelRegistry().then((r) => {
      setStatus(r?.status ?? null);
      setModels(r?.models ?? null);
    });
  }, []);

  return (
    <AdminShell>
      <h2 className="section-title" style={{ fontSize: "1.2rem" }}>
        Model registry
      </h2>
      {models === null && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          DATA UNAVAILABLE — model registry unreachable (backend or database
          not configured).
        </p>
      )}
      {models && models.length === 0 && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          {status === "no_models"
            ? "No model versions registered yet."
            : "No model versions registered yet."}
        </p>
      )}
      {models && models.length > 0 && (
        <table className="mnb-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>Champion</th>
              <th>Registered</th>
              <th>Training period</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model_version}>
                <td>
                  <strong>{m.model_version}</strong>
                </td>
                <td>
                  <span className="badge">
                    {m.champion ? "champion" : "challenger"}
                  </span>
                </td>
                <td>{m.registered_at ?? "—"}</td>
                <td>{m.training_period ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="context-note" style={{ marginTop: 24 }}>
        The ledger of settled picks is append-only and no admin action here
        can rewrite history. Model promotion is intentionally not a button in
        this panel — it requires an explicit, audited operation.
      </p>
    </AdminShell>
  );
}
