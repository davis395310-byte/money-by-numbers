"use client";

import { useEffect, useState } from "react";
import { AdminShell } from "@/components/AdminShell";
import {
  createSportsbook,
  deactivateSportsbook,
  fetchSportsbooks,
  updateSportsbook,
  type AdminApiError,
  type Sportsbook,
} from "@/lib/admin";

/**
 * Sportsbook partner management (Phase 8 admin endpoints).
 *
 * Partners start with NO affiliate URL (CTA stays disabled for them until a
 * real URL is configured). Editing or deactivating a partner never touches
 * the pick ledger — there is no write path from this page to predictions.
 */
export default function AdminSportsbooksPage() {
  const [books, setBooks] = useState<Sportsbook[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<AdminApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    affiliate_url: "",
    terms: "",
    commission_structure: "",
  });

  async function refresh() {
    const r = await fetchSportsbooks();
    setBooks(r.data?.sportsbooks ?? null);
    setNote(r.data?.note ?? null);
    setError(r.error);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) return;
    setBusy(true);
    const r = await createSportsbook({
      name: form.name.trim(),
      affiliate_url: form.affiliate_url.trim() || null,
      terms: form.terms.trim() || null,
      commission_structure: form.commission_structure.trim() || null,
    });
    setBusy(false);
    if (r.error) setError(r.error);
    else {
      setForm({ name: "", affiliate_url: "", terms: "", commission_structure: "" });
      await refresh();
    }
  }

  async function toggleActive(book: Sportsbook) {
    setBusy(true);
    const r = await updateSportsbook(book.sportsbook_id, {
      active: !book.active,
    });
    setBusy(false);
    if (r.error) setError(r.error);
    else await refresh();
  }

  async function handleDeactivate(book: Sportsbook) {
    if (
      !window.confirm(
        `Deactivate "${book.name}"? Its CTAs will stop immediately. Click history is preserved.`,
      )
    )
      return;
    setBusy(true);
    const r = await deactivateSportsbook(book.sportsbook_id);
    setBusy(false);
    if (r.error) setError(r.error);
    else await refresh();
  }

  return (
    <AdminShell>
      <h2 className="section-title" style={{ fontSize: "1.2rem" }}>
        Sportsbook partners
      </h2>
      {error && (
        <div className="empty-state" style={{ textAlign: "left" }} role="alert">
          <strong>Could not load:</strong> {error.message}
        </div>
      )}
      {books && books.length === 0 && (
        <p className="empty-state" style={{ textAlign: "left" }}>
          {note ?? "No sportsbook partnerships configured yet."} Public CTAs
          stay disabled until a partner with a real affiliate URL exists.
        </p>
      )}
      {books && books.length > 0 && (
        <table className="mnb-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>CTA</th>
              <th>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {books.map((b) => (
              <tr key={b.sportsbook_id}>
                <td>
                  <strong>{b.name}</strong>
                  <br />
                  <span style={{ fontSize: "0.8rem" }}>
                    {b.affiliate_url ?? "no URL configured"}
                  </span>
                </td>
                <td>
                  <span className="badge">
                    {b.cta_enabled ? "enabled" : "disabled"}
                  </span>
                </td>
                <td>{b.active ? "yes" : "no"}</td>
                <td>
                  <button
                    className="btn btn-ghost"
                    disabled={busy}
                    onClick={() => toggleActive(b)}
                  >
                    {b.active ? "Pause" : "Activate"}
                  </button>{" "}
                  <button
                    className="btn btn-ghost"
                    disabled={busy}
                    onClick={() => handleDeactivate(b)}
                  >
                    Deactivate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 32 }}>Add partner</h3>
      <p className="section-sub">
        URLs are stored exactly as entered and validated (http/https). Leave
        the URL empty to create a partner whose CTAs stay disabled.
      </p>
      <form onSubmit={handleCreate} style={{ display: "grid", gap: 12, maxWidth: 560 }}>
        <label>
          Name
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            style={inputStyle}
          />
        </label>
        <label>
          Affiliate URL (optional)
          <input
            value={form.affiliate_url}
            onChange={(e) => setForm({ ...form, affiliate_url: e.target.value })}
            placeholder="https://…"
            style={inputStyle}
          />
        </label>
        <label>
          Terms (optional)
          <input
            value={form.terms}
            onChange={(e) => setForm({ ...form, terms: e.target.value })}
            style={inputStyle}
          />
        </label>
        <label>
          Commission structure (optional)
          <input
            value={form.commission_structure}
            onChange={(e) => setForm({ ...form, commission_structure: e.target.value })}
            style={inputStyle}
          />
        </label>
        <div>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Add partner"}
          </button>
        </div>
      </form>
    </AdminShell>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid var(--border, #333)",
  background: "var(--bg-raised, #111)",
  color: "inherit",
  fontSize: "1rem",
  marginTop: 6,
};
