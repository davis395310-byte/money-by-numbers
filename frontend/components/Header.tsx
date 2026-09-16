"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { label: "Home", href: "/" },
  { label: "NFL", href: "/nfl" },
  { label: "Best Bets", href: "/best-bets" },
  { label: "Track Record", href: "/track-record" },
  { label: "Numby", href: "/numby" },
  { label: "How It Works", href: "/how-it-works" },
];

export function Header() {
  const pathname = usePathname();

  return (
    <header className="header">
      <div className="container header-inner">
        <Link href="/" className="brand" aria-label="Money By Numbers">
          <span className="accent-word">MONEY</span> BY NUMBERS
        </Link>
        <nav className="nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={pathname === item.href ? "active" : ""}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
