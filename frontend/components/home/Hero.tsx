import Link from "next/link";

export function Hero() {
  return (
    <section className="hero">
      <div className="container">
        <h1>
          <span className="accent-word">MONEY</span> BY NUMBERS
        </h1>
        <p className="hero-tagline">NFL Intelligence. Powered by Numbers.</p>
        <p className="hero-sub">
          Data-driven NFL predictions, matchup intelligence and market analysis.
        </p>
        <div className="hero-ctas">
          <Link href="/best-bets" className="btn btn-primary">
            See Best Bets
          </Link>
          <Link href="/how-it-works" className="btn btn-ghost">
            How the Model Works
          </Link>
        </div>
      </div>
    </section>
  );
}
