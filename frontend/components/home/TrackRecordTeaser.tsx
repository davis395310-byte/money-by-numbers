"use client";

import Link from "next/link";

/**
 * Track record teaser — honest empty state. Wins and losses will both be
 * shown here once predictions are recorded; losing picks are never hidden.
 */
export function TrackRecordTeaser() {
  return (
    <section className="section">
      <div className="container">
        <h2 className="section-title">Money By Numbers Track Record</h2>
        <p className="section-sub">
          Full transparency: every pick, every result, updated after each
          week&apos;s games.
        </p>
        <div className="empty-state">
          <span className="badge">Not live yet</span>
          <p>
            No predictions recorded yet. The track record will appear here
            once the model is live — losing picks are never hidden.
          </p>
        </div>
        <div className="mt-16">
          <Link href="/track-record" className="btn btn-ghost">
            View Track Record
          </Link>
        </div>
      </div>
    </section>
  );
}
