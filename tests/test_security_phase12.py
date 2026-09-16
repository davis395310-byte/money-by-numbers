"""Phase 12 — security middleware tests: headers, CORS, rate limits."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.middleware import configure_cors


@pytest.fixture()
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers["Permissions-Policy"]
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp


def test_hsts_absent_on_plain_http_dev(client, monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    r = client.get("/api/health")
    assert "Strict-Transport-Security" not in r.headers


def test_hsts_present_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    fresh = FastAPI()
    configure_cors(fresh)

    @fresh.get("/ping")
    def ping():
        return {"ok": True}

    r = TestClient(fresh).get("/ping")
    assert "max-age=31536000" in r.headers["Strict-Transport-Security"]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/api/numby/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_rejects_unlisted_origin(client):
    r = client.options(
        "/api/numby/chat",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_wildcard_cors_refused_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        configure_cors(FastAPI())


def test_wildcard_cors_allowed_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    configure_cors(FastAPI())  # must not raise


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _chat(client, ip):
    return client.post(
        "/api/numby/chat",
        json={"message": "hello"},
        headers={"X-Forwarded-For": ip},
    )


def test_numby_chat_rate_limited(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_NUMBY_PER_MIN", "2")
    # NOTE: settings are read per-request (get_settings is uncached), but the
    # middleware rules were built at import time with defaults (30/min). This
    # test therefore rebuilds a fresh app to pick up the lowered limit.
    fresh = FastAPI()

    @fresh.post("/api/numby/chat")
    def chat():
        return {"ok": True}

    from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware

    fresh.add_middleware(SecurityHeadersMiddleware)
    fresh.add_middleware(RateLimitMiddleware, rules=[("/api/numby/chat", 2, 60)])
    c = TestClient(fresh)
    ip = "10.9.9.9"
    assert c.post("/api/numby/chat", headers={"X-Forwarded-For": ip}).status_code == 200
    assert c.post("/api/numby/chat", headers={"X-Forwarded-For": ip}).status_code == 200
    r = c.post("/api/numby/chat", headers={"X-Forwarded-For": ip})
    assert r.status_code == 429
    assert r.json()["status"] == "rate_limited"
    assert "Retry-After" in r.headers
    # A different IP still has budget.
    assert c.post("/api/numby/chat", headers={"X-Forwarded-For": "10.9.9.10"}).status_code == 200


def test_rate_limiter_skips_preflight(monkeypatch):
    fresh = FastAPI()

    @fresh.post("/api/numby/chat")
    def chat():
        return {"ok": True}

    from app.middleware import RateLimitMiddleware

    fresh.add_middleware(RateLimitMiddleware, rules=[("/api/numby/chat", 1, 60)])
    c = TestClient(fresh)
    ip = "10.8.8.8"
    # Preflight OPTIONS must not consume the single allowed request.
    c.options(
        "/api/numby/chat",
        headers={"Origin": "http://x.example", "Access-Control-Request-Method": "POST"},
    )
    assert c.post("/api/numby/chat", headers={"X-Forwarded-For": ip}).status_code == 200


def test_429_carries_security_headers():
    fresh = FastAPI()

    @fresh.post("/api/numby/chat")
    def chat():
        return {"ok": True}

    from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware

    fresh.add_middleware(RateLimitMiddleware, rules=[("/api/numby/chat", 1, 60)])
    fresh.add_middleware(SecurityHeadersMiddleware)
    c = TestClient(fresh)
    ip = "10.7.7.7"
    c.post("/api/numby/chat", headers={"X-Forwarded-For": ip})
    r = c.post("/api/numby/chat", headers={"X-Forwarded-For": ip})
    assert r.status_code == 429
    assert r.headers["X-Content-Type-Options"] == "nosniff"
