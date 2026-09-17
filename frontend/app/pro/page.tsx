"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchPredictions,
  type Prediction,
  type PredictionsResponse,
} from "@/lib/api";

const DATA_UNAVAILABLE = "DATA UNAVAILABLE";

/**
 * Pro Picks — the paid version of Money By Numbers.
 *
 * Every locked pick, plus the tipping-point explainer: two or three
 * sentences on *why* the model picked who it picked (the rating gap,
 * home field, rest — the actual inputs of the champion model, recovered
 * from the locked pick itself).
 *
 * Explainers are a paid-tier feature. The founder's year-one-free
 * decision (2026-09-14) means they are visible to everyone for the first
 * year; after that the API gates them to paid plans and this page shows
 * the upgrade path instead. Nothing here is ever fabricated: no backend
 * or no stored picks renders DATA UNAVAILABLE.
 */

type PageState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "ok"; data: PredictionsResponse };

const RESPONSIBLE_NOTE =
  "Picks are model outputs for entertainment and education — not betting advice. Never wager more than you can afford to lose.";

function fmtPct(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

function confidenceClass(conf: string): string {
  const c = conf.toLowerCase();
  if (c === "high") return "badge badge-high";
  if (c === "low") return "badge badge-low";
  return "badge";
}

export default function ProPicksPage() {
  const [season, setSeason] = useState(2026);
  const [week, setWeek] = useState(1);
  const [state, setState] = useState<PageState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchPredictions(season, week, true).then((data) => {
      if (cancelled) return;
      setState(data ? { kind: "ok", data } : { kind: "unreachable" });
    });
    return () => {
      cancelled = true;
    };
  }, [season, week]);

  const picks: Prediction[] =
    state.kind === "ok" ? state.data.predictions : [];
  const access =
    state.kind === "ok" ? state.data.explainer_access : undefined;
  const explainersUnlocked = access?.granted ?? false;
  const yearOneFree = access?.reason === "year_one_free";

  return (
    <main className="container">
      <h1>Pro Picks</h1>
      <p className="lede">
        Every locked pick, and — for Pro members — the tipping points behind
        it: a short explanation of <em>why</em> the model picked who it
        picked, drawn from the pick&apos;s actual inputs. No narratives, no
        invented storylines.
      </p>

      {explainersUnlocked && yearOneFree && (
        <div className="card pro-banner" role="note">
          <strong>Year one is free.</strong> Tipping-point explainers are a
          paid feature, unlocked for everyone while we build in public.
        </div>
      )}

      <div className="nfl-filters">
        <label>
          Season{" "}
          <input
            type="number"
            value={season}
            min={2016}
            max={2026}
            onChange={(e) => setSeason(Number(e.target.value))}
          />
        </label>
        <label>
          Week{" "}
          <select
            value={week}
            onChange={(e) => setWeek(Number(e.target.value))}
          >
            {Array.from({ length: 22 }, (_, i) => i + 1).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.kind === "loading" && (
        <p className="empty-state">Loading picks&hellip;</p>
      )}

      {state.kind === "unreachable" && (
        <div className="empty-state">
          <strong>{DATA_UNAVAILABLE}</strong>
          <p>The API could not be reached from this browser.</p>
        </div>
      )}

      {state.kind === "ok" && state.data.status !== "ok" && (
        <div className="empty-state">
          <strong>{DATA_UNAVAILABLE}</strong>
          <p>
            {state.data.reason ?? "No picks stored yet for this week."}
          </p>
        </div>
      )}

      {state.kind === "ok" && state.data.status === "ok" && (
        <div className="card-grid">
          {picks.map((pick) => (
            <article className="card pro-pick" key={pick.prediction_id}>
              <div className="pro-pick-head">
                <span className="pro-matchup">
                  {pick.away_team} @ {pick.home_team}
                </span>
                <span className={confidenceClass(pick.confidence)}>
                  {pick.confidence.toLowerCase()}
                </span>
              </div>
              <div className="pro-pick-body">
                <span className="pro-winner">{pick.predicted_winner}</span>
                <span className="pro-prob">{fmtPct(pick.model_probability)}</span>
              </div>
              {pick.predicted_home_score !== null &&
                pick.predicted_away_score !== null && (
                  <p className="pro-score">
                    Model score: {pick.away_team} {pick.predicted_away_score}{" "}
                    — {pick.home_team} {pick.predicted_home_score}
                  </p>
                )}

              <div className="pro-why">
                <h3>Why this pick</h3>
                {explainersUnlocked && pick.explainer ? (
                  <>
                    <p>{pick.explainer}</p>
                    {pick.tipping_points && pick.tipping_points.length > 0 && (
                      <ul className="pro-factors">
                        {pick.tipping_points.map((tp) => (
                          <li key={tp.factor}>
                            <strong>{tp.label}:</strong> {tp.detail}
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : explainersUnlocked ? (
                  <p className="pro-why-empty">
                    Explainer not generated for this pick yet.
                  </p>
                ) : (
                  <div className="pro-locked">
                    <p>
                      Tipping-point explainers are a Pro feature.{" "}
                      <Link href="/pricing">See plans</Link>
                    </p>
                  </div>
                )}
              </div>

              <p className="pro-meta">
                {pick.model_version} · locked{" "}
                {new Date(pick.created_at).toLocaleString()}
              </p>
            </article>
          ))}
        </div>
      )}

      <p className="bb-note">{RESPONSIBLE_NOTE}</p>
    </main>
  );
}
