"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { setAdminKey, type AdminApiError } from "@/lib/admin";
import { API_URL } from "@/lib/config";

/**
 * Admin login — key entry only. The key is checked against the backend
 * (a lightweight admin call) and stored in sessionStorage on success.
 * The key is never persisted beyond the tab session.
 */
export default function AdminLoginPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AdminApiError | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = key.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    // Validate the key with a cheap admin call before storing it.
    try {
      const res = await fetch(`${API_URL}/api/admin/affiliate/stats`, {
          headers: { "X-Admin-Key": trimmed, Accept: "application/json" },
          cache: "no-store",
        },
      );
      if (res.status === 501) {
        const body = await res.json().catch(() => ({}));
        setError({
          kind: "unconfigured",
          message:
            body.detail ??
            "Admin API is not configured on the server (ADMIN_API_KEY unset).",
        });
      } else if (res.status === 403) {
        setError({ kind: "forbidden", message: "Invalid admin key." });
      } else if (!res.ok) {
        setError({
          kind: "error",
          status: res.status,
          message: `Server returned status ${res.status}.`,
        });
      } else {
        setAdminKey(trimmed);
        router.replace("/admin");
      }
    } catch {
      setError({
        kind: "unreachable",
        message: "Could not reach the backend. Is it running?",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container">
      <section className="section" style={{ maxWidth: 480 }}>
        <h1 className="section-title">Admin sign-in</h1>
        <p className="section-sub">
          Enter the admin key (<code>ADMIN_API_KEY</code>). It is kept only in
          this tab&apos;s session storage — closing the tab signs you out.
        </p>
        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
          <label>
            <span style={{ display: "block", marginBottom: 6 }}>Admin key</span>
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoComplete="off"
              placeholder="••••••••"
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: 8,
                border: "1px solid var(--border, #333)",
                background: "var(--bg-raised, #111)",
                color: "inherit",
                fontSize: "1rem",
              }}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Checking…" : "Sign in"}
          </button>
        </form>
        {error && (
          <div
            className="empty-state"
            style={{ marginTop: 16, textAlign: "left" }}
            role="alert"
          >
            <strong>
              {error.kind === "unconfigured"
                ? "Admin not configured"
                : error.kind === "forbidden"
                  ? "Invalid key"
                  : error.kind === "unreachable"
                    ? "Backend unreachable"
                    : "Error"}
            </strong>
            <p>{error.message}</p>
            {error.kind === "unconfigured" && (
              <p>
                Set <code>ADMIN_API_KEY</code> on the backend to enable the
                admin panel. There is no default key.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
