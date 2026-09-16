"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { AdminGuard } from "@/components/AdminGuard";
import { clearAdminKey } from "@/lib/admin";

const ADMIN_NAV = [
  { label: "Overview", href: "/admin" },
  { label: "Sportsbooks", href: "/admin/sportsbooks" },
  { label: "Scheduler", href: "/admin/scheduler" },
  { label: "Subscriptions", href: "/admin/subscriptions" },
  { label: "Models", href: "/admin/models" },
];

/**
 * AdminShell — shared chrome for every admin page: guard, section nav, and
 * an explicit sign-out that clears the session key.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  function signOut() {
    clearAdminKey();
    router.replace("/admin/login");
  }

  return (
    <AdminGuard>
      <div className="container">
        <section className="section">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <h1 className="section-title" style={{ margin: 0 }}>
              Admin
            </h1>
            <button className="btn btn-ghost" onClick={signOut}>
              Sign out
            </button>
          </div>
          <nav
            aria-label="Admin"
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              marginTop: 16,
              marginBottom: 24,
            }}
          >
            {ADMIN_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`btn ${pathname === item.href ? "btn-primary" : "btn-ghost"}`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          {children}
        </section>
      </div>
    </AdminGuard>
  );
}
