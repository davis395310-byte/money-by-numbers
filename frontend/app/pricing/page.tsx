"use client";

import { useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import {
  fetchBillingStatus,
  formatPrice,
  openCustomerPortal,
  startCheckout,
  type BillingPlan,
  type BillingStatus,
} from "@/lib/api";

/**
 * Pricing — subscription plans (Phase 9).
 *
 * YEAR ONE IS FREE (founder decision, 2026-09-14): every plan's paid
 * features are free for our first year while we build in public and earn
 * trust before revenue. `YEAR_ONE_FREE` gates the paid subscribe CTAs;
 * set it to false when year one ends and the Stripe catalog takes over.
 *
 * Everything else on this page comes from GET /api/billing/status
 * (backend/app/routers/billing.py). Plan prices come from Stripe via the
 * API; until Stripe is configured they render as DATA UNAVAILABLE — this
 * page never fabricates a price, plan, or subscription state.
 */

// Founder commitment: the entire first year is free. Flip to false when
// year one ends to re-enable paid subscriptions.
const YEAR_ONE_FREE = true;

type PageState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "ok"; billing: BillingStatus };

export default function PricingPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [checkoutFlag, setCheckoutFlag] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBillingStatus().then((billing) => {
      if (cancelled) return;
      setState(billing ? { kind: "ok", billing } : { kind: "unreachable" });
    });
    const params = new URLSearchParams(window.location.search);
    const flag = params.get("checkout");
    if (flag === "success" || flag === "cancelled") setCheckoutFlag(flag);
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubscribe(plan: BillingPlan) {
    setNotice(null);
    setBusyPlan(plan.plan);
    const res = await startCheckout(plan.plan);
    setBusyPlan(null);
    if (res.ok && res.data) {
      window.location.href = res.data.checkout_url;
      return;
    }
    if (res.status === 401) {
      setNotice("Sign in first — subscriptions need an account.");
    } else if (res.status === 501) {
      setNotice("Subscriptions aren't available yet — billing isn't configured.");
    } else if (res.status === 0) {
      setNotice("Couldn't reach the server. Try again in a moment.");
    } else {
      setNotice("Couldn't start checkout. Try again in a moment.");
    }
  }

  async function handleManage() {
    setNotice(null);
    const res = await openCustomerPortal();
    if (res.ok && res.data) {
      window.location.href = res.data.portal_url;
      return;
    }
    setNotice(
      res.status === 404
        ? "No active subscription found for this account."
        : "Couldn't open subscription management. Try again in a moment.",
    );
  }

  return (
    <main style={styles.page}>
      <h1 style={styles.h1}>Pricing</h1>
      {YEAR_ONE_FREE && (
        <div style={styles.yearOneBanner}>
          <strong>Year One is free.</strong> Every prediction, every edge, the
          full track record — free for our entire first year. We&apos;re
          building this in the open and we&apos;d rather earn your trust than
          your wallet. Paid plans arrive in year two.
        </div>
      )}
      <p style={styles.sub}>
        Money By Numbers is free-first: every prediction, edge, and track-record
        number is free forever. Paid plans buy higher limits and extras —
        subscriptions are our secondary revenue; sportsbook referrals are the
        primary one.
      </p>

      {checkoutFlag === "success" && (
        <div style={styles.bannerOk}>
          Payment received — your plan updates automatically once Stripe
          confirms it. This usually takes under a minute.
        </div>
      )}
      {checkoutFlag === "cancelled" && (
        <div style={styles.banner}>Checkout was cancelled — nothing was charged.</div>
      )}
      {notice && <div style={styles.banner}>{notice}</div>}

      {state.kind === "loading" && <p style={styles.muted}>Loading plans…</p>}
      {state.kind === "unreachable" && (
        <p style={styles.muted}>
          DATA UNAVAILABLE — couldn't reach the server. Plans can't be shown
          right now.
        </p>
      )}

      {state.kind === "ok" && (
        <>
          {!state.billing.stripe_configured && (
            <div style={styles.banner}>
              Subscriptions aren't available yet — billing isn't configured.
              Everything below is the plan catalog; prices show once Stripe is
              connected.
            </div>
          )}
          {state.billing.current_user.signed_in ? (
            <p style={styles.muted}>
              Your current plan:{" "}
              <strong>{planName(state.billing, state.billing.current_user.plan)}</strong>
              {state.billing.current_user.subscription && (
                <> ({state.billing.current_user.subscription.status})</>
              )}{" "}
              {state.billing.current_user.subscription?.cancel_at_period_end &&
                "— set to cancel at period end."}
            </p>
          ) : (
            <p style={styles.muted}>
              You're browsing signed out — <Link href="/api/auth/google/login">sign in</Link>{" "}
              to subscribe or manage a plan.
            </p>
          )}
          <div style={styles.grid}>
            {state.billing.plans.map((plan) => (
              <PlanCard
                key={plan.plan}
                plan={plan}
                current={state.billing.current_user.plan === plan.plan}
                stripeConfigured={state.billing.stripe_configured}
                busy={busyPlan === plan.plan}
                onSubscribe={() => handleSubscribe(plan)}
              />
            ))}
          </div>
          {state.billing.current_user.subscription && (
            <button style={styles.manage} onClick={handleManage}>
              Manage subscription
            </button>
          )}
        </>
      )}
    </main>
  );
}

function planName(billing: BillingStatus, plan: string): string {
  return billing.plans.find((p) => p.plan === plan)?.display_name ?? plan;
}

function PlanCard({
  plan,
  current,
  stripeConfigured,
  busy,
  onSubscribe,
}: {
  plan: BillingPlan;
  current: boolean;
  stripeConfigured: boolean;
  busy: boolean;
  onSubscribe: () => void;
}) {
  const isFree = plan.plan === "free";
  const purchasable =
    !YEAR_ONE_FREE && !isFree && stripeConfigured && plan.price_configured;
  const ctaTitle = YEAR_ONE_FREE
    ? "Year One is free — paid plans begin in year two"
    : !stripeConfigured
      ? "Billing isn't configured yet"
      : !plan.price_configured
        ? "No price configured for this plan"
        : `Subscribe to ${plan.display_name}`;
  return (
    <section style={{ ...styles.card, ...(current ? styles.cardCurrent : {}) }}>
      <h2 style={styles.planName}>{plan.display_name}</h2>
      <p style={styles.tagline}>{plan.tagline}</p>
      <p style={styles.price}>{isFree ? "Free" : formatPrice(plan.price)}</p>
      {!isFree && plan.price.note && (
        <p style={styles.priceNote}>{plan.price.note}</p>
      )}
      <ul style={styles.features}>
        {plan.features.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
      {current ? (
        <p style={styles.currentBadge}>Current plan</p>
      ) : isFree ? (
        <p style={styles.muted}>No sign-up needed.</p>
      ) : (
        <button
          style={styles.cta}
          disabled={!purchasable || busy}
          onClick={onSubscribe}
          title={ctaTitle}
        >
          {busy ? "Starting checkout…" : `Subscribe to ${plan.display_name}`}
        </button>
      )}
    </section>
  );
}

const styles: Record<string, CSSProperties> = {
  page: { maxWidth: 960, margin: "0 auto", padding: "24px 16px 64px" },
  h1: { fontSize: 28, margin: "8px 0" },
  sub: { color: "#555", maxWidth: 640, lineHeight: 1.5 },
  muted: { color: "#666" },
  banner: {
    background: "#fff8e1",
    border: "1px solid #f0d060",
    borderRadius: 8,
    padding: "12px 16px",
    margin: "16px 0",
  },
  bannerOk: {
    background: "#e8f5e9",
    border: "1px solid #81c784",
    borderRadius: 8,
    padding: "12px 16px",
    margin: "16px 0",
  },
  yearOneBanner: {
    background: "#f3efff",
    border: "1px solid #8B5CF6",
    borderRadius: 8,
    padding: "14px 18px",
    margin: "16px 0",
    lineHeight: 1.5,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: 16,
    marginTop: 16,
  },
  card: {
    border: "1px solid #ddd",
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  cardCurrent: { border: "2px solid #1a73e8" },
  planName: { fontSize: 20, margin: 0 },
  tagline: { color: "#555", margin: 0, minHeight: 40 },
  price: { fontSize: 24, fontWeight: 700, margin: "8px 0 0" },
  priceNote: { color: "#888", fontSize: 13, margin: 0 },
  features: { paddingLeft: 18, margin: "8px 0", lineHeight: 1.6 },
  currentBadge: { fontWeight: 700, color: "#1a73e8", marginTop: "auto" },
  cta: {
    marginTop: "auto",
    padding: "10px 16px",
    borderRadius: 8,
    border: "none",
    background: "#1a73e8",
    color: "#fff",
    fontWeight: 600,
    cursor: "pointer",
  },
  manage: {
    marginTop: 24,
    padding: "10px 16px",
    borderRadius: 8,
    border: "1px solid #999",
    background: "#fff",
    cursor: "pointer",
  },
};
