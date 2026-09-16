"""Business document models: users, sessions, affiliate, subscriptions, analytics, scheduler."""

import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

# Fields that MUST be supplied by the caller when recording an affiliate click.
# The destination URL is never invented by this service.
REQUIRED_CLICK_FIELDS: tuple = ("sportsbook", "cta_location", "timestamp", "destination")


class User(BaseModel):
    user_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    email: EmailStr
    name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    google_sub: Optional[str] = None


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class AffiliateClick(BaseModel):
    click_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    game_id: Optional[str] = None
    sportsbook: str
    line: Optional[str] = None
    odds: Optional[str] = None
    model_probability: Optional[float] = Field(default=None, ge=0, le=1)
    market_probability: Optional[float] = Field(default=None, ge=0, le=1)
    edge: Optional[float] = None
    model_version: Optional[str] = None
    cta_location: str
    campaign: Optional[str] = None
    timestamp: datetime
    # The real destination URL supplied by the caller (e.g. from the
    # campaign's configured affiliate link). Never fabricated here.
    destination: str


class AffiliateCampaign(BaseModel):
    campaign_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    sportsbook: str
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Phase 8 — sportsbook partners and conversions. Partner records live in the
# ``sportsbooks`` collection (admin-configured). The collection starts EMPTY:
# no partnerships are invented, and no affiliate URL is ever hardcoded.
# ---------------------------------------------------------------------------


def validate_affiliate_url(url: Optional[str]) -> Optional[str]:
    """Validate a configured affiliate URL.

    Returns the URL unchanged when it is None (allowed: partner configured
    without a URL yet — CTAs stay disabled) or a well-formed http(s) URL.
    Raises ValueError for anything else. URLs are never invented here; this
    only guards what an admin stores.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError(
            "affiliate_url must be an http(s) URL or null; "
            "refusing to store a non-web destination."
        )
    return url


class Sportsbook(BaseModel):
    """A sportsbook affiliate partner (admin-configured, Phase 8)."""

    sportsbook_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    # The real affiliate URL. None = partner known but no URL configured yet:
    # CTAs for this sportsbook stay disabled until a URL exists.
    affiliate_url: Optional[str] = None
    terms: Optional[str] = None
    # Commission structure as free text entered by the admin (e.g. "25% rev
    # share"). Never invented by the platform.
    commission_structure: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(
            self, "affiliate_url", validate_affiliate_url(self.affiliate_url)
        )


class Conversion(BaseModel):
    """A recorded affiliate conversion (admin-entered real data, Phase 8).

    Conversions arrive from sportsbook affiliate reports, which the admin
    transcribes. Every figure here must reflect a real report — the platform
    never invents revenue.
    """

    conversion_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sportsbook_id: str
    sportsbook: str  # name snapshot at record time
    click_id: Optional[str] = None  # link back to affiliate_clicks when known
    converted_at: datetime
    # Revenue actually reported for this conversion. None = not reported
    # (shown as "not reported", never as zero revenue).
    revenue_amount: Optional[float] = Field(default=None, ge=0)
    currency: str = "USD"
    notes: Optional[str] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    recorded_by: str = "admin"


class Subscription(BaseModel):
    """A user's subscription (Phase 9, spec 51-54, ``subscriptions`` collection).

    Plans: free (generous default), starter, pro, agency (spec 53).
    Status is written ONLY by the Stripe webhook handler from verified
    Stripe events — never from client input.
    """

    subscription_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    plan: Literal["free", "starter", "pro", "agency"] = "free"
    status: Literal[
        "active", "canceled", "past_due", "trialing", "incomplete", "unpaid"
    ] = "active"
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StripeWebhookEvent(BaseModel):
    """One received Stripe webhook event (Phase 9 idempotency record).

    ``processed`` is set after the event's side effects are applied; a
    redelivered event with the same ``event_id`` is acknowledged without
    re-applying anything.
    """

    event_id: str
    event_type: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    processed: bool = False
    note: Optional[str] = None


class UsageCounter(BaseModel):
    """Per-identity daily usage counters (Phase 9 plan gating).

    ``key`` is ``user:<user_key>`` for signed-in users or
    ``anon:<session_id>`` for anonymous traffic; ``day`` is YYYY-MM-DD UTC.
    """

    key: str
    day: str
    numby_questions: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BetAlert(BaseModel):
    """A premium (starter+) bet alert (Phase 9 plan-gated feature)."""

    alert_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    name: str = Field(min_length=1, max_length=120)
    team: Optional[str] = None  # validated NFL abbr when present
    game_id: Optional[str] = None
    edge_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AnalyticsEvent(BaseModel):
    event: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class SchedulerJob(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    schedule: str
    status: Literal["scheduled", "running", "paused", "failed"] = "scheduled"
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None


def build_click_record(**fields: Any) -> Dict[str, Any]:
    """Build a validated affiliate click record dict.

    Required: sportsbook, cta_location, timestamp, destination.
    Raises ValueError listing any missing required fields. The destination
    URL must be supplied by the caller and is never invented here.
    """
    missing = [f for f in REQUIRED_CLICK_FIELDS if fields.get(f) is None]
    if missing:
        raise ValueError(
            "Missing required affiliate click field(s): "
            + ", ".join(missing)
            + ". destination must be supplied by the caller; it is never invented."
        )
    click = AffiliateClick(**fields)
    return click.model_dump(mode="json")
