"use client";

import { useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import {
  fetchCtaStatus,
  fetchEdges,
  fetchOddsStatus,
  referralUrl,
  type ConfidenceTier,
  type CtaSportsbook,
  type Edge,
} from "@/lib/api";
import { AFFILIATE_DISCLOSURE, RESPONSIBLE_GAMBLING_NOTICE } from "@/lib/config";

/**
 * Best Bets — the ranked list of model edges vs. market lines.
 *
 * This page renders ONLY numbers returned by GET /api/edges and
 * GET /api/odds/status (backend/app/routers/odds.py). Every empty state
 * says DATA UNAVAILABLE or names the honest reason (no API key, missing
 * predictions/odds, backend unreachable). Nothing is fabricated.
 *
 * Sportsbook CTAs render ONLY when GET /api/affiliate/cta-status reports
 * ctas_enabled: true (server-side gate, backend/app/routers/affiliate.py).
 * With zero configured partnerships, no CTA buttons appear at all — no
 * "coming soon" buttons implying partnerships exist. CTA links hit the
 * backend's tracked redirect (/r/{sportsbook_id}); affiliate URLs are
 * never exposed to the client.
 *
 * Local stored-edges fallback: when no odds API key is configured (local
 * dev) but the backend still has a stored odds snapshot, the page renders
 * those stored edges with an explicit as-of label instead of the no-key
 * empty state. Snapshots older than one refresh cycle (7 days, see
 * docs/best-bets-stored-edges-policy.md) show "refresh pending" instead.
 * This branch cannot fire when a key is configured, so production is
 * unchanged.
 */

type PageState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "odds_unavailable"; reason?: string }
  | { kind: "edges_unavailable"; missing: string[]; reason?: string }
  | { kind: "ok"; edges: Edge[] }
  | { kind: "stored_edges"; edges: Edge[]; asOf: string }
  | { kind: "stored_stale"; asOf?: string };

/** Exact honest copy for the no-API-key state (per product spec). */
const NO_KEY_COPY =
  "Live odds unavailable — no API key configured. Edges can't be computed without market lines.";

function fmtAmerican(price: number | null): string {
  if (price === null || price === undefined) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

function fmtPct(frac: number): string {
  return `${(frac * 100).toFixed(1)}%`;
}

function fmtEV(ev: number | null): string {
  if (ev === null || ev === undefined) return "—";
  const sign = ev > 0 ? "+" : ev < 0 ? "−" : "";
  return `${sign}${Math.abs(ev).toFixed(2)}`;
}

function fmtLine(edge: Edge): string {
  if (edge.market === "moneyline" || edge.line === null) return "—";
  const prefix =
    edge.market === "total" ? (edge.side === "over" ? "O " : "U ") : "";
  const signed = edge.line > 0 ? `+${edge.line}` : `${edge.line}`;
  return `${prefix}${signed}`;
}

/**
 * Game ids follow "{season}_{week:02d}_{away}_{home}" (e.g. 2026_01_NE_SEA).
 * Anything else is shown verbatim — never guessed at.
 */
function gameLabel(edge: Edge): string {
  const parts = edge.game_id.split("_");
  if (parts.length >= 4 && parts[2] && parts[3]) {
    return `${parts[2]} @ ${parts[3]}`;
  }
  return edge.game_id;
}

/** Format an ISO snapshot timestamp as a readable as-of label. */
function fmtAsOf(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
/** The /api/edges "missing" detail arrives as "missing: a, b, ..." in reason. */
function parseMissing(reason?: string): string[] {
  if (!reason) return [];
  const prefix = "missing: ";
  if (!reason.startsWith(prefix)) return [];
  return reason
    .slice(prefix.length)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function badgeStyle(tier: ConfidenceTier): CSSProperties {
  const color =
    tier === "HIGH"
      ? "var(--accent)"
      : tier === "MEDIUM"
        ? "var(--warn)"
        : "var(--text-dim)";
  return {
    display: "inline-block",
    fontSize: "0.72rem",
    fontWeight: 800,
    letterSpacing: "0.08em",
    color,
    border: `1px solid ${color}`,
    borderRadius: 999,
    padding: "3px 10px",
    whiteSpace: "nowrap",
  };
}

function LoadingSkeleton() {
  return (
    <div className="card card-grid" aria-label="Loading best bets">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="bb-skeleton-bar"
          style={{ width: `${92 - i * 12}%` }}
        />
      ))}
    </div>
  );
}

/** Ranked edges table + explainer, shared by the live and stored views. */
function EdgesTable({ edges }: { edges: Edge[] }) {
  return (
    <>
      <div className="card" style={{ padding: 0 }}>
        <div className="bb-table-wrap">
          <table className="bb-table">
            <thead>
              <tr>
                <th>Game</th>
                <th>Market</th>
                <th>Side</th>
                <th style={{ textAlign: "right" }}>Line</th>
                <th style={{ textAlign: "right" }}>Price</th>
                <th style={{ textAlign: "right" }}>Model prob</th>
                <th style={{ textAlign: "right" }}>No-vig implied</th>
                <th style={{ textAlign: "right" }}>Edge</th>
                <th style={{ textAlign: "right" }}>EV / unit</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {edges.map((edge) => (
                <tr key={`${edge.game_id}-${edge.market}-${edge.side}`}>
                  <td>{gameLabel(edge)}</td>
                  <td style={{ textTransform: "capitalize" }}>{edge.market}</td>
                  <td style={{ textTransform: "capitalize" }}>{edge.side}</td>
                  <td className="bb-num">{fmtLine(edge)}</td>
                  <td className="bb-num">{fmtAmerican(edge.price)}</td>
                  <td className="bb-num">{fmtPct(edge.model_probability)}</td>
                  <td className="bb-num">{fmtPct(edge.novig_implied)}</td>
                  <td
                    className="bb-num"
                    style={{
                      color: "var(--accent)",
                      fontWeight: 700,
                    }}
                  >
                    {fmtPct(edge.edge)}
                  </td>
                  <td className="bb-num">{fmtEV(edge.ev)}</td>
                  <td>
                    <span style={badgeStyle(edge.confidence)}>
                      {edge.confidence}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="bb-note">
        Edge = model probability − no-vig implied probability. A play is
        listed when edge ≥ 3%. EV is expected value per unit staked at the
        listed price. Confidence is assigned by the edge engine from the model
        probability (HIGH ≥ 68%, MEDIUM ≥ 58%, otherwise LOW). Model outputs
        are estimates, not guarantees.
      </p>
    </>
  );
}

export default function BestBetsPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [ctaBooks, setCtaBooks] = useState<CtaSportsbook[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchEdges(), fetchOddsStatus(), fetchCtaStatus()]).then(
      ([edges, oddsStatus, ctaStatus]) => {
        if (cancelled) return;
        setCtaBooks(
          ctaStatus && ctaStatus.ctas_enabled ? ctaStatus.sportsbooks : []
        );
        if (edges === null && oddsStatus === null) {
          setState({ kind: "unreachable" });
        } else if (oddsStatus?.status === "odds_unavailable") {
          // Local stored-edges fallback: no live odds key is configured,
          // but the backend still has a stored odds snapshot joined with
          // predictions. Render it with an explicit as-of label; hide it
          // entirely once the snapshot is stale. This branch only fires
          // when no API key is configured, so the live-key (production)
          // path is untouched.
          if (edges !== null && edges.status === "ok") {
            const ranked = [...(edges.edges ?? [])].sort(
              (a, b) => b.edge - a.edge
            );
            if (edges.snapshot_stale || !edges.snapshot_as_of) {
              setState({
                kind: "stored_stale",
                asOf: edges.snapshot_as_of,
              });
            } else {
              setState({
                kind: "stored_edges",
                edges: ranked,
                asOf: edges.snapshot_as_of,
              });
            }
          } else {
            setState({
              kind: "odds_unavailable",
              reason: oddsStatus.reason,
            });
          }
        } else if (
          edges !== null &&
          (edges.status === "edges_unavailable" || edges.status === "error")
        ) {
          const missing = parseMissing(edges.reason);
          const reason =
            edges.reason && !edges.reason.startsWith("missing: ")
              ? edges.reason
              : undefined;
          setState({ kind: "edges_unavailable", missing, reason });
        } else if (
          edges !== null &&
          (edges.status === "ok" || edges.status === "no_edges")
        ) {
          const ranked = [...(edges.edges ?? [])].sort(
            (a, b) => b.edge - a.edge
          );
          setState({ kind: "ok", edges: ranked });
        } else {
          setState({
            kind: "edges_unavailable",
            missing: ["edges endpoint"],
            reason: "The /api/edges endpoint did not respond.",
          });
        }
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <style>{`
        .bb-skeleton-bar {
          height: 18px;
          border-radius: 6px;
          background: linear-gradient(90deg, var(--bg-elev) 25%, var(--border) 50%, var(--bg-elev) 75%);
          background-size: 200% 100%;
          animation: bb-shimmer 1.4s infinite linear;
        }
        @keyframes bb-shimmer {
          from { background-position: 200% 0; }
          to { background-position: -200% 0; }
        }
        .bb-table-wrap {
          overflow-x: auto;
          -webkit-overflow-scrolling: touch;
        }
        .bb-table {
          width: 100%;
          min-width: 760px;
          border-collapse: collapse;
          font-size: 0.88rem;
        }
        .bb-table th {
          text-align: left;
          font-size: 0.72rem;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: var(--text-dim);
          font-weight: 700;
          padding: 10px 12px;
          border-bottom: 1px solid var(--border);
          white-space: nowrap;
        }
        .bb-table td {
          padding: 12px;
          border-bottom: 1px solid var(--border);
          white-space: nowrap;
        }
        .bb-table tr:last-child td {
          border-bottom: none;
        }
        .bb-table tbody tr:hover {
          background: var(--bg-elev);
        }
        .bb-num {
          font-variant-numeric: tabular-nums;
          text-align: right;
        }
        .bb-note {
          color: var(--text-dim);
          font-size: 0.85rem;
          margin-top: 16px;
          max-width: 720px;
        }
      `}</style>

      <section className="placeholder-hero">
        <div className="container">
          <h1>Best Bets</h1>
          <p style={{ color: "var(--text-dim)", marginTop: 8, maxWidth: 640 }}>
            The plays where the model&apos;s numbers diverge most from
            sportsbook lines — ranked by edge, with transparent reasoning.
          </p>
        </div>
      </section>

      <section className="placeholder-body">
        <div className="container">
          {state.kind === "loading" && <LoadingSkeleton />}

          {state.kind === "unreachable" && (
            <div className="empty-state">
              <span className="badge">Data unavailable</span>
              <p>
                DATA UNAVAILABLE — could not reach the backend API. Check that
                the backend is running and try again.
              </p>
              <div className="mt-16">
                <Link href="/" className="btn btn-ghost">
                  Back to Home
                </Link>
              </div>
            </div>
          )}

          {state.kind === "odds_unavailable" && (
            <div className="empty-state">
              <span className="badge">Data unavailable</span>
              <p>{NO_KEY_COPY}</p>
              {state.reason && state.reason !== NO_KEY_COPY && (
                <p style={{ fontSize: "0.85rem" }}>{state.reason}</p>
              )}
              <p style={{ fontSize: "0.85rem" }}>
                Edges compare model probabilities against live market lines.
                Configure an odds API key on the backend to enable this page.
              </p>
              <div className="mt-16">
                <Link href="/" className="btn btn-ghost">
                  Back to Home
                </Link>
              </div>
            </div>
          )}

          {state.kind === "edges_unavailable" && (
            <div className="empty-state">
              <span className="badge">Data unavailable</span>
              <p>DATA UNAVAILABLE — edges can&apos;t be computed right now.</p>
              {state.missing.length > 0 && (
                <p>
                  Missing:{" "}
                  <strong style={{ color: "var(--text)" }}>
                    {state.missing.join(", ")}
                  </strong>
                  . Edges need both model predictions and market odds.
                </p>
              )}
              {state.reason && (
                <p style={{ fontSize: "0.85rem" }}>{state.reason}</p>
              )}
              <div className="mt-16">
                <Link href="/" className="btn btn-ghost">
                  Back to Home
                </Link>
              </div>
            </div>
          )}

          {state.kind === "ok" &&
            (state.edges.length === 0 ? (
              <div className="empty-state">
                <span className="badge">Data unavailable</span>
                <p>
                  DATA UNAVAILABLE — no edges currently meet the edge
                  threshold. The model and market lines are in agreement, or
                  there are no upcoming games with both predictions and odds.
                </p>
              </div>
            ) : (
              <>
                {ctaBooks !== null && ctaBooks.length > 0 && (
                  <div
                    className="card"
                    style={{ marginBottom: 16 }}
                    aria-label="Sportsbook offers"
                  >
                    <p
                      style={{
                        fontWeight: 700,
                        marginBottom: 4,
                      }}
                    >
                      Ready to place a bet?
                    </p>
                    <p
                      style={{
                        color: "var(--text-dim)",
                        fontSize: "0.85rem",
                        marginBottom: 12,
                      }}
                    >
                      These sportsbooks have active offers through Money By
                      Numbers. Clicks are tracked; availability of any specific
                      line at a sportsbook is not guaranteed.
                    </p>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      {ctaBooks.map((book) => (
                        <a
                          key={book.sportsbook_id}
                          className="btn btn-primary"
                          href={`${referralUrl(book.sportsbook_id)}?cta_location=best_bets`}
                          target="_blank"
                          rel="sponsored nofollow noopener"
                        >
                          Bet at {book.name}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                <EdgesTable edges={state.edges} />
              </>
            ))}

          {state.kind === "stored_stale" && (
            <div className="empty-state">
              <span className="badge">Refresh pending</span>
              <p>
                DATA UNAVAILABLE — the stored lines
                {state.asOf ? ` (as of ${fmtAsOf(state.asOf)})` : ""} are older
                than one refresh cycle and are no longer shown. Refresh the
                odds feed to see current edges.
              </p>
              <div className="mt-16">
                <Link href="/" className="btn btn-ghost">
                  Back to Home
                </Link>
              </div>
            </div>
          )}

          {state.kind === "stored_edges" && (
            <>
              <div
                className="card"
                style={{ marginBottom: 16 }}
                aria-label="Stored lines notice"
              >
                <p style={{ fontWeight: 700, marginBottom: 4 }}>
                  Stored lines — as of {fmtAsOf(state.asOf)}
                </p>
                <p
                  style={{ color: "var(--text-dim)", fontSize: "0.85rem" }}
                >
                  Live odds are unavailable, so these edges come from a stored
                  snapshot taken {fmtAsOf(state.asOf)}. Lines may have moved
                  since — treat these as a reference, not current prices.
                  Model outputs are estimates, not guarantees.
                </p>
              </div>
              <EdgesTable edges={state.edges} />
            </>
          )}

          <p className="bb-note">
            {ctaBooks !== null && ctaBooks.length > 0 ? (
              <>
                Betting links above are tracked affiliate referrals to
                sportsbooks with configured partnerships.{" "}
              </>
            ) : (
              <>
                Betting links: no sportsbook CTAs are shown on this page.
                Affiliate partnerships are not configured yet, so all
                sportsbook CTAs remain disabled until real affiliate URLs
                exist.{" "}
              </>
            )}
            {AFFILIATE_DISCLOSURE}
          </p>
          <p className="bb-note">{RESPONSIBLE_GAMBLING_NOTICE}</p>
        </div>
      </section>
    </>
  );
}
