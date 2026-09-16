"""Application configuration.

All credentials and integration keys come from environment variables.
Every field is optional with a None default so the app boots honestly
without any integrations configured; endpoints must report
"not_configured" states rather than pretend an integration is wired.
"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    APP_VERSION: str = "0.1.0-phase1"

    # MongoDB
    MONGODB_URI: Optional[str] = None
    MONGODB_DB: Optional[str] = None

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_AUTH_REDIRECT_URI: Optional[str] = None

    # Sessions
    SESSION_SECRET: Optional[str] = None

    # Stripe
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_PUBLISHABLE_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    # Price IDs for the three paid plans (spec 53). Set from the Stripe
    # dashboard; never hardcoded. A plan with no price ID configured cannot
    # be purchased — checkout returns an honest 501 for it.
    STRIPE_PRICE_ID_STARTER: Optional[str] = None
    STRIPE_PRICE_ID_PRO: Optional[str] = None
    STRIPE_PRICE_ID_AGENCY: Optional[str] = None

    # Public base URL of the frontend, used for Stripe Checkout
    # success/cancel redirects. Defaults to local dev.
    FRONTEND_URL: Optional[str] = None

    # Odds provider
    ODDS_API_KEY: Optional[str] = None
    ODDS_API_BASE_URL: Optional[str] = None

    # Numby AI assistant (Phase 7)
    AI_PROVIDER: Optional[str] = None  # "openai" (default) or an https:// base URL
    AI_API_KEY: Optional[str] = None
    AI_MODEL: Optional[str] = None  # default: gpt-4o-mini

    # Affiliate (Phase 8). Partner URLs live in the sportsbooks collection
    # (admin-configured), never in env. Only the disclosure text and the
    # admin gate key are env-configurable.
    AFFILIATE_DISCLOSURE_TEXT: Optional[str] = None
    ADMIN_API_KEY: Optional[str] = None

    # Security (Phase 12)
    # ENVIRONMENT: "development" | "production". Production refuses a
    # wildcard CORS origin and enables HSTS unconditionally.
    ENVIRONMENT: str = "development"
    # Comma-separated list of allowed CORS origins. Defaults to the local
    # dev frontend. Production MUST set this to the real frontend domain(s);
    # a wildcard ("*") is rejected at boot when ENVIRONMENT=production.
    CORS_ORIGINS: str = "http://localhost:3000"
    # Per-IP per-minute rate limits on the most abuse-sensitive public
    # endpoints. In-memory sliding window (documented limitation: not shared
    # across worker processes — use a gateway/WAF limit for hard guarantees).
    RATE_LIMIT_NUMBY_PER_MIN: int = 30
    RATE_LIMIT_REDIRECT_PER_MIN: int = 60


def get_settings() -> Settings:
    """Return a fresh Settings instance read from the current environment.

    Not cached on purpose: health checks must reflect the environment as it
    is at request time, and tests can safely mutate os.environ.
    """
    return Settings()
