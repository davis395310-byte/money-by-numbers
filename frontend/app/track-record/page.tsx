"use client";

import { useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import {
  fetchTrackRecordCalibration,
  fetchTrackRecordPicks,
  fetchTrackRecordSummary,
  fetchTrackRecordTiers,
  type RecordSummary,
  type TrackedPick,
  type TrackRecordCalibrationResponse,
  type TrackRecordSummaryResponse,
  type TrackRecordTiersResponse,
} from "@/lib/api";

/**
 * Track Record — the public, immutable ledger of every published pick.
 *
 * Every number on this page is computed live from the pick-results ledger
 * (GET /api/track-record/*). Losing picks are never removed: the ledger is
 * append-only and the API exposes no write routes for it.
 */

function fmtPct(frac: number | null): string {
  if (frac === null || frac === undefined) return "—";
  return `${(frac * 100).toFixed(1)}%`;
}

function fmtUnits(units: number | null): string {
  if (units === null || units === undefined) return "—";
  const sign = units > 0 ? "+" : units < 0 ? "−" : "";
  return `${sign}${Math.abs(units).toFixed(2)}u`;
}

function fmtAmerican(price: number | null): string {
  if (price === null || price === undefined) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

function fmtLine(pick: TrackedPick): string {
  if (pick.market === "moneyline" || pick.market_line_at_pick === null)
    return "ML";
  const line = pick.market_line_at_pick;
  const signed = line > 0 ? `+${line}` : `${line}`;
  if (pick.market === "total")
    return `${pick.side === "over" ? "O" : "U"} ${signed}`;
  return signed;
}

function gameLabel(pick: TrackedPick): string {
  const parts = pick.game_id.split("_");
  if (parts.length >= 4 && parts[2] && parts[3])
    return `${parts[2]} @ ${parts[3]}`;
  const away = pick.away_team ?? "?";
  const home = pick.home_team ?? "?";
  return `${away} @ ${home}`;
}

function resultBadge(result?: string): CSSProperties {
  const color =
    result === "win"
      ? "var(--accent)"
      : result === "loss"
        ? "var(--danger, #e5484d)"
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

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-dim)", fontWeight: 700 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: "0.8rem", color: "var(--text-dim)", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

/**
 * Historical backtest — the AUDITED champion record.
 *
 * This section is NOT the live track record. It describes the one-time,
 * leakage-audited walk-forward backtest of the registered champion model
 * MNB-NFL-2026.01 (Elo family) over the 2016–2025 regular seasons
 * (2,639 games), reproduced byte-identically on 2026-09-13
 * (md5 454d3767c33b334fb87749ee5f90dc41, leakage harness clean).
 * Each season was predicted using only data from earlier seasons — the model
 * never saw the future. All ten seasons are included; none were dropped.
 * Backtests describe the past; they are not a guarantee of future results
 * and must never be confused with the immutable live pick ledger above.
 *
 * Per-season correct counts below reproduce the audited folds exactly; the
 * downloadable ledger's aggregates match them 1-for-1.
 */
const BACKTEST_SEASONS = [
  { season: 2016, n: 256, correct: 154, acc: 60.16, brier: 0.236 },
  { season: 2017, n: 256, correct: 167, acc: 65.23, brier: 0.2273 },
  { season: 2018, n: 256, correct: 162, acc: 63.28, brier: 0.2331 },
  { season: 2019, n: 256, correct: 161, acc: 62.89, brier: 0.2288 },
  { season: 2020, n: 256, correct: 160, acc: 62.5, brier: 0.2355 },
  { season: 2021, n: 272, correct: 172, acc: 63.24, brier: 0.2374 },
  { season: 2022, n: 271, correct: 160, acc: 59.04, brier: 0.244 },
  { season: 2023, n: 272, correct: 157, acc: 57.72, brier: 0.2443 },
  { season: 2024, n: 272, correct: 180, acc: 66.18, brier: 0.2188 },
  { season: 2025, n: 272, correct: 167, acc: 61.4, brier: 0.2392 },
];

function BacktestSection() {
  return (
    <div className="card" style={{ marginTop: 28, borderStyle: "dashed" }}>
      <span
        className="badge"
        style={{
          background: "transparent",
          border: "1px solid var(--warning, #f5a623)",
          color: "var(--warning, #f5a623)",
          textDecoration: "none",
        }}
      >
        Historical backtest — not live results
      </span>
      <h2 className="section-title" style={{ marginTop: 12 }}>
        10-season backtest: 2016–2025
      </h2>
      <p style={{ color: "var(--text-dim)", fontSize: "0.88rem", maxWidth: 720 }}>
        A one-time, leakage-audited walk-forward test of the registered
        champion model <strong>MNB-NFL-2026.01</strong> (Elo family) that
        powers the live picks. Each season was predicted using only data
        from earlier seasons — the model never saw the future. All ten
        seasons are included; none were dropped. The backtest was reproduced
        byte-identically and the leakage harness found zero violations.
      </p>

      <div className="tr-grid" style={{ marginTop: 16 }}>
        <StatCard label="Winner accuracy" value="62.14%" sub="1,640 correct of 2,639 games" />
        <StatCard label="Brier score" value="0.2344" sub="lower is better; 0.25 ≈ coin flip" />
        <StatCard label="vs spread (ATS)" value="50.66%" sub="1,304 of 2,574 lined games — no betting edge" />
        <StatCard label="Full ledger" value="CSV" sub="every pick, downloadable below" />
      </div>

      <div style={{ marginBottom: 20 }}>
        <a
          href="/track-record/MNB_2016_2025_game_ledger.csv"
          download="MNB_2016_2025_game_ledger.csv"
          className="btn btn-primary"
        >
          Download the full 2,639-game ledger (CSV)
        </a>
        <p className="tr-note" style={{ marginTop: 8 }}>
          One row per game: pre-game Elo, model probability, predicted winner,
          actual score, and whether the pick was correct. The file&apos;s
          totals reproduce the audited backtest exactly — 1,640/2,639.
          Check it yourself.
        </p>
      </div>

      <h3 className="section-title" style={{ fontSize: "1rem" }}>Season by season</h3>
      <div className="tr-table-wrap">
        <table className="tr-table">
          <thead>
            <tr>
              <th>Season</th><th className="tr-num">Games</th>
              <th className="tr-num">Correct</th><th className="tr-num">Accuracy</th>
              <th className="tr-num">Brier</th>
            </tr>
          </thead>
          <tbody>
            {BACKTEST_SEASONS.map((s) => (
              <tr key={s.season}>
                <td><strong>{s.season}</strong></td>
                <td className="tr-num">{s.n}</td>
                <td className="tr-num">{s.correct}</td>
                <td className="tr-num">{s.acc.toFixed(2)}%</td>
                <td className="tr-num">{s.brier.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="section-title" style={{ fontSize: "1rem", marginTop: 20 }}>How the number was produced</h3>
      <p style={{ color: "var(--text-dim)", fontSize: "0.88rem", maxWidth: 720 }}>
        The champion is a margin-aware Elo system, nothing more exotic: every
        team starts at 1500; after each game ratings update zero-sum with
        K&nbsp;=&nbsp;20 on a 400-point scale, weighted by a 538-style
        margin-of-victory multiplier. The home team gets a 35-point Elo bump
        in the <em>expectation only</em> — stored ratings stay home-neutral so
        no home-field bias leaks into the ratings themselves. The side with
        the higher win probability is the pick. That is the entire model, and
        the ledger above is everything it ever said about 2016–2025.
      </p>

      <h3 className="section-title" style={{ fontSize: "1rem", marginTop: 20 }}>Audit trail</h3>
      <p style={{ color: "var(--text-dim)", fontSize: "0.88rem", maxWidth: 720 }}>
        Walk-forward with a kickoff-ordered leakage harness: state updates
        from a game are applied only after every game with an equal-or-earlier
        kickoff has been scored. Result: <strong>zero leakage violations</strong> across
        2,639 games. Independent reproduction of the full backtest was
        byte-identical (md5&nbsp;<code>454d3767c33b334fb87749ee5f90dc41</code>).
        Model registered as <strong>MNB-NFL-2026.01</strong>; the registry is
        append-only and the champion cannot be silently replaced.
      </p>

      <h3 className="section-title" style={{ fontSize: "1rem", marginTop: 20 }}>Other tested variants</h3>
      <div className="tr-table-wrap">
        <table className="tr-table">
          <thead>
            <tr><th>Variant</th><th className="tr-num">Accuracy</th><th>Status</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>Elo + efficiency factors (logistic)</td>
              <td className="tr-num">63.8%</td>
              <td>Research candidate — tested, not promoted to production</td>
            </tr>
            <tr>
              <td>Gradient-boosted factors (LightGBM)</td>
              <td className="tr-num">61.9%</td>
              <td>Did not beat the Elo baseline</td>
            </tr>
            <tr>
              <td>Models using the closing line (~66.7%)</td>
              <td className="tr-num">66.7%</td>
              <td>Market-informed — measures the market, not a deployable edge</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="tr-note">
        <strong>Read this carefully:</strong> a backtest is a laboratory result,
        not a track record. It tells you the model had a real signal
        (62.14% vs 54.5% for blindly picking the home team over the same
        games), and it tells you the spread record is a coin flip — there is
        no betting edge here. Past performance does not guarantee future
        results. The live ledger at the top of this page is the only record
        that counts going forward.
      </p>
    </div>
  );
}

type PageState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "empty"; pending: number }
  | {
      kind: "ok";
      summary: TrackRecordSummaryResponse;
      tiers: TrackRecordTiersResponse;
      calibration: TrackRecordCalibrationResponse;
      picks: TrackedPick[];
    };

export default function TrackRecordPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchTrackRecordSummary(),
      fetchTrackRecordTiers(),
      fetchTrackRecordCalibration(),
      fetchTrackRecordPicks(),
    ]).then(([summary, tiers, calibration, picksRes]) => {
      if (cancelled) return;
      if (!summary && !tiers && !calibration && !picksRes) {
        setState({ kind: "unreachable" });
        return;
      }
      if (summary && summary.status === "no_settled_picks") {
        setState({ kind: "empty", pending: summary.pending_picks ?? 0 });
        return;
      }
      if (summary && summary.status === "ok") {
        setState({
          kind: "ok",
          summary,
          tiers: tiers ?? { status: "error", tiers: null },
          calibration: calibration ?? { status: "error", buckets: null },
          picks: picksRes?.picks ?? [],
        });
        return;
      }
      setState({ kind: "unreachable" });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <style>{`
        .tr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .tr-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        .tr-table { width: 100%; min-width: 720px; border-collapse: collapse; font-size: 0.88rem; }
        .tr-table th { text-align: left; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); font-weight: 700; padding: 10px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
        .tr-table td { padding: 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
        .tr-table tr:last-child td { border-bottom: none; }
        .tr-num { font-variant-numeric: tabular-nums; text-align: right; }
        .tr-note { color: var(--text-dim); font-size: 0.85rem; margin-top: 16px; max-width: 720px; }
        .tr-skeleton { height: 18px; border-radius: 6px; background: linear-gradient(90deg, var(--bg-elev) 25%, var(--border) 50%, var(--bg-elev) 75%); background-size: 200% 100%; animation: tr-shimmer 1.4s infinite linear; }
        @keyframes tr-shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
      `}</style>

      <section className="placeholder-hero">
        <div className="container">
          <h1>Track Record</h1>
          <p style={{ color: "var(--text-dim)", marginTop: 8, maxWidth: 640 }}>
            Every pick the model has ever published — wins, losses, and
            pushes — graded from an immutable ledger. Losing picks are never
            hidden or removed.
          </p>
        </div>
      </section>

      <section className="placeholder-body">
        <div className="container">
          {state.kind === "loading" && (
            <div className="card card-grid" aria-label="Loading track record">
              {[0, 1, 2].map((i) => (
                <div key={i} className="tr-skeleton" style={{ width: `${92 - i * 12}%` }} />
              ))}
            </div>
          )}

          {state.kind === "unreachable" && (
            <div className="empty-state">
              <span className="badge">Data unavailable</span>
              <p>
                DATA UNAVAILABLE — could not reach the backend API. Check that
                the backend is running and try again.
              </p>
              <div className="mt-16">
                <Link href="/" className="btn btn-ghost">Back to Home</Link>
              </div>
            </div>
          )}

          {state.kind === "empty" && (
            <div className="empty-state">
              <span className="badge">No settled picks yet</span>
              <p>
                No picks have been settled yet. Published picks appear here as
                pending, and results land after each game goes final.
              </p>
              {state.pending > 0 && (
                <p style={{ fontSize: "0.85rem" }}>
                  {state.pending} pick{state.pending === 1 ? "" : "s"} currently
                  pending settlement.
                </p>
              )}
              <div className="mt-16">
                <Link href="/how-it-works" className="btn btn-ghost">
                  How the model is tested
                </Link>
              </div>
            </div>
          )}

          {state.kind === "ok" && state.summary.summary && (
            <>
              <div className="tr-grid">
                <StatCard label="Record" value={`${state.summary.summary.wins}–${state.summary.summary.losses}${state.summary.summary.pushes ? `–${state.summary.summary.pushes}` : ""}`} sub={`${state.summary.summary.n} settled picks`} />
                <StatCard label="Accuracy" value={fmtPct(state.summary.summary.accuracy)} sub="wins ÷ decided (pushes excluded)" />
                <StatCard label="Units" value={fmtUnits(state.summary.summary.roi_units)} sub={state.summary.summary.roi_n > 0 ? `over ${state.summary.summary.roi_n} priced picks` : "no priced picks yet"} />
                <StatCard label="Avg Brier" value={state.summary.summary.avg_brier !== null ? state.summary.summary.avg_brier.toFixed(4) : "—"} sub={`n=${state.summary.summary.brier_n} moneyline picks`} />
              </div>

              {state.tiers.tiers && (
                <div className="card" style={{ marginBottom: 20 }}>
                  <h2 className="section-title">By confidence tier</h2>
                  <div className="tr-table-wrap">
                    <table className="tr-table">
                      <thead>
                        <tr>
                          <th>Tier</th><th className="tr-num">Picks</th>
                          <th className="tr-num">W–L–P</th>
                          <th className="tr-num">Accuracy</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(["HIGH", "MEDIUM", "LOW"] as const).map((tier) => {
                          const t: RecordSummary = state.tiers.tiers![tier];
                          return (
                            <tr key={tier}>
                              <td><strong>{tier}</strong></td>
                              <td className="tr-num">{t.n}</td>
                              <td className="tr-num">{t.wins}–{t.losses}–{t.pushes}</td>
                              <td className="tr-num">{fmtPct(t.accuracy)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {state.calibration.buckets && state.calibration.buckets.length > 0 && (
                <div className="card" style={{ marginBottom: 20 }}>
                  <h2 className="section-title">Calibration</h2>
                  <p style={{ color: "var(--text-dim)", fontSize: "0.85rem", marginBottom: 12 }}>
                    When the model says 70%, does it win ~70% of the time?
                  </p>
                  <div className="tr-table-wrap">
                    <table className="tr-table">
                      <thead>
                        <tr>
                          <th>Predicted</th><th className="tr-num">Picks</th>
                          <th className="tr-num">Observed win rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {state.calibration.buckets.map((b) => (
                          <tr key={b.prob_low}>
                            <td>{fmtPct(b.prob_low)}–{fmtPct(b.prob_high)}</td>
                            <td className="tr-num">{b.n}</td>
                            <td className="tr-num">{fmtPct(b.observed_win_rate)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <div className="card" style={{ marginBottom: 20 }}>
                <h2 className="section-title">Recent picks</h2>
                {state.picks.length === 0 ? (
                  <p style={{ color: "var(--text-dim)" }}>No picks published yet.</p>
                ) : (
                  <div className="tr-table-wrap">
                    <table className="tr-table">
                      <thead>
                        <tr>
                          <th>Game</th><th>Pick</th><th>Line</th>
                          <th className="tr-num">Model</th><th>Tier</th><th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {state.picks.slice(0, 25).map((p) => (
                          <tr key={p.prediction_id}>
                            <td>{gameLabel(p)}</td>
                            <td>{p.predicted_winner} <span style={{ color: "var(--text-dim)" }}>({p.market})</span></td>
                            <td className="tr-num">{fmtLine(p)} {p.market_price_at_pick !== null ? fmtAmerican(p.market_price_at_pick) : ""}</td>
                            <td className="tr-num">{fmtPct(p.model_probability)}</td>
                            <td style={{ textTransform: "uppercase", fontSize: "0.75rem", fontWeight: 700 }}>{p.confidence}</td>
                            <td>
                              {p.settlement_status === "pending" ? (
                                <span style={resultBadge("pending")}>PENDING</span>
                              ) : (
                                <span style={resultBadge(p.result)}>
                                  {(p.result ?? "ungraded").toUpperCase()}
                                  {p.actual_home_score !== null && p.actual_home_score !== undefined
                                    ? ` ${p.actual_away_score}–${p.actual_home_score}` : ""}
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="tr-note">
                  {state.summary.note ?? "Every number above is computed from the immutable pick ledger. Losing picks are never removed."}
                  {state.summary.pending_picks ? ` ${state.summary.pending_picks} pick(s) pending settlement.` : ""}
                </p>
              </div>
            </>
          )}

          {/* Historical backtest: always visible, clearly separated from the live ledger. */}
          <BacktestSection />
        </div>
      </section>
    </>
  );
}
