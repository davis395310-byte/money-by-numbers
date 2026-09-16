"""Security middleware — Phase 12.

Three independent layers, all configured from environment:

1. ``SecurityHeadersMiddleware`` — adds HTTP security headers to every
   response. HSTS is sent when the request arrived over HTTPS or when
   ``ENVIRONMENT=production`` (production must terminate TLS at the
   load balancer / host).
2. ``RateLimitMiddleware`` — in-memory per-IP sliding-window rate limits
   for the most abuse-sensitive public endpoints (Numby chat, which costs
   LLM/provider budget, and the tracked affiliate redirect). Returns 429
   with ``Retry-After`` when exceeded. Documented limitation: state is
   per-process, so it does not aggregate across workers — treat it as a
   first line of defense behind a gateway/WAF limit in production.
3. CORS is configured in ``main.py`` via Starlette's CORSMiddleware with
   explicit origins only; a wildcard origin is refused at boot in
   production (see ``configure_cors``).
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, List, Tuple

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import get_settings


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        # API-only service: no content needs to execute; deny framing.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        settings = get_settings()
        if settings.ENVIRONMENT == "production" or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limit for configured path prefixes.

    ``rules`` maps a path prefix to (requests, window_seconds). Matching is
    prefix-based so ``/r/{sportsbook_id}`` covers every sportsbook.
    """

    def __init__(self, app, rules: List[Tuple[str, int, int]]):
        super().__init__(app)
        self.rules = rules
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    def _client_ip(self, request: Request) -> str:
        # Respect X-Forwarded-For only for the leftmost (client) entry when
        # set by a trusted proxy is out of scope here; production should run
        # behind a gateway that enforces its own limits anyway.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # CORS preflights must never consume rate-limit budget.
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        for prefix, limit, window in self.rules:
            if not path.startswith(prefix):
                continue
            key = (prefix, self._client_ip(request))
            now = time.monotonic()
            window_hits = self._hits.setdefault(key, deque())
            while window_hits and window_hits[0] <= now - window:
                window_hits.popleft()
            if len(window_hits) >= limit:
                retry_after = int(window_hits[0] + window - now) + 1
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "rate_limited",
                        "reason": f"Too many requests to {prefix}; slow down.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            window_hits.append(now)
            break  # first matching rule wins
        return await call_next(request)


# ---------------------------------------------------------------------------
# CORS configuration
# ---------------------------------------------------------------------------


def parse_origins(raw: str) -> List[str]:
    """Split the CORS_ORIGINS env string into an explicit origin list."""
    return [o.strip() for o in raw.split(",") if o.strip()]


def configure_cors(app: FastAPI) -> List[str]:
    """Attach CORSMiddleware with explicit origins; wire security middleware.

    Raises ``RuntimeError`` at boot if ENVIRONMENT=production and the origin
    list contains a wildcard — production must name its frontend domains.
    """
    settings = get_settings()
    origins = parse_origins(settings.CORS_ORIGINS)
    if settings.ENVIRONMENT == "production" and "*" in origins:
        raise RuntimeError(
            "Refusing to boot: CORS_ORIGINS contains '*' while "
            "ENVIRONMENT=production. Set explicit frontend origins."
        )
    # Added in reverse-execution order: CORS is innermost (closest to the
    # route), then rate limiting, then security headers outermost so every
    # response — including 429s and CORS rejections — carries the headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )
    app.add_middleware(
        RateLimitMiddleware,
        rules=[
            ("/api/numby/chat", settings.RATE_LIMIT_NUMBY_PER_MIN, 60),
            ("/r/", settings.RATE_LIMIT_REDIRECT_PER_MIN, 60),
        ],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    return origins
