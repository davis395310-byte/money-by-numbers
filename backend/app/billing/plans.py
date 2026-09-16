"""Plan definitions (Phase 9, spec 53).

Plans: free / starter / pro / agency. Limits are enforced server-side by
``billing.identity``. Prices are NOT stored here — they are fetched from
Stripe at request time (see ``billing.stripe_client``); until Stripe is
configured every paid plan reports DATA UNAVAILABLE for its price.
"""

from typing import Any, Dict, List, Optional

from ..config import get_settings

PLAN_ORDER: tuple = ("free", "starter", "pro", "agency")

PLANS: Dict[str, Dict[str, Any]] = {
    "free": {
        "display_name": "Free",
        "tagline": "Full NFL intelligence, free forever.",
        "price_env": None,
        "numby_questions_per_day": 20,
        "csv_export": False,
        "bet_alerts": False,
        "seats": 1,
        "features": [
            "Every game prediction with probabilities",
            "Model edges vs market lines",
            "Full public track record",
            "20 Numby questions per day",
        ],
    },
    "starter": {
        "display_name": "Starter",
        "tagline": "For fans who want more answers.",
        "price_env": "STRIPE_PRICE_ID_STARTER",
        "numby_questions_per_day": 100,
        "csv_export": True,
        "bet_alerts": True,
        "seats": 1,
        "features": [
            "Everything in Free",
            "100 Numby questions per day",
            "CSV export of the pick ledger",
            "Bet alerts on your teams",
        ],
    },
    "pro": {
        "display_name": "Pro",
        "tagline": "For serious students of the game.",
        "price_env": "STRIPE_PRICE_ID_PRO",
        "numby_questions_per_day": 1000,
        "csv_export": True,
        "bet_alerts": True,
        "seats": 1,
        "features": [
            "Everything in Starter",
            "1,000 Numby questions per day",
            "Priority data refreshes",
        ],
    },
    "agency": {
        "display_name": "Agency",
        "tagline": "For content teams and syndicates.",
        "price_env": "STRIPE_PRICE_ID_AGENCY",
        "numby_questions_per_day": 1000,
        "csv_export": True,
        "bet_alerts": True,
        "seats": 5,
        "features": [
            "Everything in Pro",
            "5 seats included",
            "Shared bet-alert workspace",
        ],
    },
}


def plan_rank(plan: str) -> int:
    """Numeric rank for plan comparisons; unknown plans rank as free."""
    try:
        return PLAN_ORDER.index(plan)
    except ValueError:
        return 0


def plan_meets(plan: str, minimum: str) -> bool:
    """True when ``plan`` is at least ``minimum`` tier."""
    return plan_rank(plan) >= plan_rank(minimum)


def paid_plans() -> List[str]:
    return [p for p in PLAN_ORDER if p != "free"]


def price_id_for_plan(plan: str) -> Optional[str]:
    """The configured Stripe price ID for a plan, or None when unset.

    Read from the environment at call time — never hardcoded, never logged.
    """
    env_name = (PLANS.get(plan) or {}).get("price_env")
    if not env_name:
        return None
    return getattr(get_settings(), env_name, None) or None


def plan_for_price_id(price_id: Optional[str]) -> Optional[str]:
    """Reverse-map a Stripe price ID to a plan name (None when unknown)."""
    if not price_id:
        return None
    for plan in paid_plans():
        if price_id_for_plan(plan) == price_id:
            return plan
    return None
