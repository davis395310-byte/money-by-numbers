"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchDataHealth, type DataHealth } from "@/lib/api";

type FetchState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "loaded"; data: DataHealth };

/**
 * This Week's NFL Games — renders game-count status from the data health
 * endpoint. Shows an honest empty state until game ingestion is configured.
 */
export function WeekGames() {
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchDataHealth().then((result) => {
      if (cancelled) return;
      setState(
        result === null
          ? { kind: "unreachable" }
          : { kind: "loaded", data: result }
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const gameCount =
    state.kind === "loaded" ? state.data.games.record_count : null;

  return (
    <section className="section">
      <div className="container">
        <h2 className="section-title">This Week&apos;s NFL Games</h2>
        <p className="section-sub">
          The full slate, matchup breakdowns, and where the numbers point.
        </p>
        {state.kind === "loading" && (
          <div className="card">
            <p>Loading game data…</p>
          </div>
        )}
        {state.kind === "unreachable" && (
          <div className="empty-state">
            <span className="badge">Data unavailable</span>
            <p>DATA UNAVAILABLE — could not reach the API.</p>
          </div>
        )}
        {state.kind === "loaded" &&
          (gameCount === 0 || gameCount === null ? (
            <div className="empty-state">
              <span className="badge">Data unavailable</span>
              <p>
                DATA UNAVAILABLE — game data ingestion not yet configured.
                The weekly slate will appear here once ingestion is live.
              </p>
            </div>
          ) : (
            <div className="card">
              <p>
                <strong>{gameCount}</strong> games ingested. Detailed matchup
                views arrive with the NFL section.
              </p>
            </div>
          ))}
        <div className="mt-16">
          <Link href="/nfl" className="btn btn-ghost">
            Explore the NFL Section
          </Link>
        </div>
      </div>
    </section>
  );
}
