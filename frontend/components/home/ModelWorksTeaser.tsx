import Link from "next/link";

/**
 * Brief plain-English teaser of how the model works.
 */
export function ModelWorksTeaser() {
  return (
    <section className="section">
      <div className="container">
        <h2 className="section-title">How the Model Works</h2>
        <p className="section-sub">
          No magic, no hype — just probability. The model compares its own
          predicted game outcomes against sportsbook lines and flags the
          biggest gaps. We publish every pick, win or lose, so you can judge
          the numbers for yourself.
        </p>
        <Link href="/how-it-works" className="btn btn-ghost">
          Read How It Works
        </Link>
      </div>
    </section>
  );
}
