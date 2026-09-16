import Link from "next/link";

interface PlaceholderProps {
  title: string;
  lede: string;
  bullets: string[];
}

export function PlaceholderPage({ title, lede, bullets }: PlaceholderProps) {
  return (
    <>
      <section className="placeholder-hero">
        <div className="container">
          <h1>{title}</h1>
        </div>
      </section>
      <section className="placeholder-body">
        <div className="container">
          <div className="card card-grid">
            <p>{lede}</p>
            <ul style={{ paddingLeft: 20, color: "var(--text-dim)" }}>
              {bullets.map((b) => (
                <li key={b} style={{ marginBottom: 6 }}>
                  {b}
                </li>
              ))}
            </ul>
            <div>
              <Link href="/" className="btn btn-ghost">
                Back to Home
              </Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
