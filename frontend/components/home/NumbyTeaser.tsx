import Link from "next/link";

/**
 * Ask Numby teaser card — honest about the timeline, nothing fake.
 */
export function NumbyTeaser() {
  return (
    <section className="section">
      <div className="container">
        <h2 className="section-title">Ask Numby</h2>
        <p className="section-sub">
          Our AI assistant for talking through matchups, lines, and the
          model&apos;s numbers.
        </p>
        <div className="card">
          <h3 style={{ marginBottom: 8 }}>Numby arrives in Phase 7</h3>
          <p style={{ color: "var(--text-dim)" }}>
            Numby isn&apos;t live yet. When it launches, you&apos;ll be able
            to ask it anything about this week&apos;s slate — and it will
            answer from the model&apos;s actual numbers, not guesses.
          </p>
          <div className="mt-16">
            <Link href="/numby" className="btn btn-ghost">
              Learn More
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
