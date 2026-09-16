"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchMethodology,
  type MethodologyResponse,
  type WalkForwardResults,
} from "@/lib/api";

/**
 * How It Works — the public methodology page.
 *
 * The walk-forward results below are NOT hardcoded: they are read from the
 * real training artifacts via GET /api/methodology (win or lose — including
 * the finding that the simple Elo model beat the complex ensemble).
 */

function fmtPct(frac: number | null | undefined): string {
  if (frac === null || frac === undefined) return "—";
  return `${(frac * 100).toFixed(2)}%`;
}

function MethodologySection({ data }: { data: WalkForwardResults }) {
  const rows = data.per_season ?? [];
  return (
    <div className="card" style={{ marginTop: 20 }}>
      <h2 className="section-title">Measured performance: 2016–2025 walk-forward</h2>
      <p style={{ color: "var(--text-dim)", fontSize: "0.9rem", maxWidth: 720 }}>
        The model was tested the honest way: trained only on seasons before
        the one being predicted, rolling forward across {data.n_games.toLocaleString()}{" "}
        games from {data.seasons[0]}–{data.seasons[data.seasons.length - 1]}.
        A leakage harness blocks any feature that peeks past kickoff
        (status: <strong>{data.leakage_harness}</strong>).
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: 12,
          margin: "16px 0",
        }}
      >
        <div>
          <div style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-dim)", fontWeight: 700 }}>
            Champion (Elo) accuracy
          </div>
          <div style={{ fontSize: "1.5rem", fontWeight: 800 }}>{fmtPct(data.elo_baseline_accuracy)}</div>
        </div>
        <div>
          <div style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-dim)", fontWeight: 700 }}>
            Ensemble accuracy
          </div>
          <div style={{ fontSize: "1.5rem", fontWeight: 800 }}>{fmtPct(data.ensemble_accuracy)}</div>
        </div>
        <div>
          <div style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-dim)", fontWeight: 700 }}>
            ATS vs closing lines
          </div>
          <div style={{ fontSize: "1.5rem", fontWeight: 800 }}>{fmtPct(data.ats_accuracy)}</div>
          <div style={{ fontSize: "0.8rem", color: "var(--text-dim)" }}>n={data.ats_n.toLocaleString()}</div>
        </div>
      </div>
      <p style={{ color: "var(--text-dim)", fontSize: "0.9rem", maxWidth: 720 }}>
        <strong>The honest headline:</strong> the complex ensemble
        ({fmtPct(data.ensemble_accuracy)}) did <em>not</em> beat the simple Elo
        model ({fmtPct(data.elo_baseline_accuracy)}) — so the platform uses the
        Elo lineage until a challenger proves better. No improvement is
        claimed. Against closing lines it managed {fmtPct(data.ats_accuracy)}:
        no demonstrated edge over the market.
      </p>
      {rows.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: 12 }}>
          <table className="mnb-table" style={{ minWidth: 560 }}>
            <thead>
              <tr>
                <th>Season</th>
                <th style={{ textAlign: "right" }}>Games</th>
                <th style={{ textAlign: "right" }}>Ensemble</th>
                <th style={{ textAlign: "right" }}>Elo baseline</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.season}>
                  <td>{r.season}</td>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.n_games}</td>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmtPct(r.accuracy)}</td>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmtPct(r.elo_accuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p style={{ color: "var(--text-dim)", fontSize: "0.82rem", marginTop: 12 }}>
        Model {data.model_version} · trained {data.training_date?.slice(0, 10)} ·{" "}
        {data.training_period}
      </p>
    </div>
  );
}

export default function HowItWorksPage() {
  const [methodology, setMethodology] = useState<MethodologyResponse | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMethodology().then((m) => {
      if (!cancelled) {
        setMethodology(m);
        setLoaded(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <section className="placeholder-hero">
        <div className="container">
          <h1>How It Works</h1>
        </div>
      </section>
      <section className="placeholder-body">
        <div className="container card-grid">
          <div className="card">
            <h2 className="section-title">1. Ingest the data</h2>
            <p style={{ color: "var(--text-dim)" }}>
              We pull in the raw inputs every prediction needs: game results,
              team and player stats, injury reports, weather forecasts, and
              live sportsbook odds.
            </p>
          </div>
          <div className="card">
            <h2 className="section-title">2. Model each matchup</h2>
            <p style={{ color: "var(--text-dim)" }}>
              The model turns that data into a probability for each outcome —
              who wins, by how much, and how many points get scored. Not
              guesses: math, expressed as percentages.
            </p>
          </div>
          <div className="card">
            <h2 className="section-title">3. Find the edge</h2>
            <p style={{ color: "var(--text-dim)" }}>
              We compare the model&apos;s probabilities against what
              sportsbooks are offering. When our number says a bet wins more
              often than the price implies, that&apos;s an edge — and edges
              get published as Best Bets.
            </p>
          </div>
          <div className="card">
            <h2 className="section-title">4. Show everything</h2>
            <p style={{ color: "var(--text-dim)" }}>
              Every pick is recorded publicly, and the track record shows
              wins <em>and</em> losses. Losing picks are never hidden. If the
              model slumps, you&apos;ll see it here before anyone else tells
              you.
            </p>
          </div>
          <div className="card">
            <h2 className="section-title">The honest fine print</h2>
            <p style={{ color: "var(--text-dim)" }}>
              Model outputs are estimates, not guarantees. Even a good model
              loses bets — that&apos;s probability, not failure. 21+ only, and
              never bet more than you can afford to lose.
            </p>
          </div>

          {loaded && methodology?.status === "ok" && methodology.walk_forward && (
            <MethodologySection data={methodology.walk_forward} />
          )}

          {loaded && methodology?.research_note && (
            <div className="card" style={{ marginTop: 20 }}>
              <h2 className="section-title">Independent model research</h2>
              <p style={{ whiteSpace: "pre-wrap", color: "var(--text-dim)", fontSize: "0.9rem" }}>
                {methodology.research_note}
              </p>
            </div>
          )}

          {loaded &&
            (!methodology || methodology.status !== "ok" || !methodology.walk_forward) && (
              <div className="card" style={{ marginTop: 20 }}>
                <h2 className="section-title">Measured performance</h2>
                <p style={{ color: "var(--text-dim)" }}>
                  DATA UNAVAILABLE — the training artifacts could not be
                  reached. Backtest numbers are published here only when they
                  can be read from the real measured results.
                </p>
              </div>
            )}

          {loaded && methodology?.status === "ok" && methodology.limits && (
            <div className="card" style={{ marginTop: 20 }}>
              <h2 className="section-title">What these numbers don&apos;t mean</h2>
              <ul style={{ paddingLeft: 20, color: "var(--text-dim)" }}>
                {methodology.limits.map((l) => (
                  <li key={l} style={{ marginBottom: 6 }}>{l}</li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <Link href="/best-bets" className="btn btn-primary">
              See Best Bets
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
