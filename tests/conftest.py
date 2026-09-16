"""Pytest configuration: make the backend package importable and keep
integration-sensitive env vars out of the test environment."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_integration_env(monkeypatch):
    """Ensure integration credentials are absent so tests exercise the
    honest 'not_configured' code paths regardless of the ambient shell."""
    for var in (
        "MONGODB_URI",
        "MONGODB_DB",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_AUTH_REDIRECT_URI",
        "SESSION_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_PUBLISHABLE_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_ID_STARTER",
        "STRIPE_PRICE_ID_PRO",
        "STRIPE_PRICE_ID_AGENCY",
        "FRONTEND_URL",
        "ODDS_API_KEY",
        "AI_PROVIDER",
        "AI_API_KEY",
        "AI_MODEL",
        "AFFILIATE_DISCLOSURE_TEXT",
        "ADMIN_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
