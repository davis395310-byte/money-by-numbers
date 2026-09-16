"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchDataHealth, type DataHealth } from "@/lib/api";

/**
 * Best Model Edges — homepage placeholder.
 * Will render the model's highest-edge plays once the model is live.
 * No edges endpoint exists yet, so this always renders an honest empty
 * state (the health fetch confirms the API is reachable).
 */
export function ModelEdges() {
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDataHealth().then((result) => {
      if (!cancelled) setApiReachable(result !== null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="section">
      <div className="container">
        <h2 className="section-title">Best Model Edges</h2>
        <p className="section-sub">
          The plays where our model sees the biggest gap between its numbers
          and the market.
        </p>
        <div className="empty-state">
          <span className="badge">Data unavailable</span>
          <p>
            DATA UNAVAILABLE — model edges not yet computed. Once the model is
            live, the highest-edge plays will appear here.{" "}
            {apiReachable === false &&
              "(The API is currently unreachable — check that the backend is running.)"}
          </p>
        </div>
        <div className="mt-16">
          <Link href="/best-bets" className="btn btn-ghost">
            View All Best Bets
          </Link>
        </div>
      </div>
    </section>
  );
}
