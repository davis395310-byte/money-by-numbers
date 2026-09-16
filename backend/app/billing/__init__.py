"""Billing (Phase 9, spec 51-54) — Stripe subscriptions, plan gating.

Subscriptions are SECONDARY revenue (affiliate is primary) and the free
tier stays generous: the full predictions/edges/track-record experience
works without paying. Paid plans buy higher Numby question limits, CSV
exports of the pick ledger, and bet alerts.

Honesty rules:
- Plan prices are fetched from Stripe at request time, never hardcoded.
  Until Stripe is configured, prices are DATA UNAVAILABLE.
- Subscription status is written ONLY by the webhook handler from
  signature-verified Stripe events — never from client input.
- No raw card details are ever stored or touched.
"""

from . import identity, plans, stripe_client, webhooks  # noqa: F401
