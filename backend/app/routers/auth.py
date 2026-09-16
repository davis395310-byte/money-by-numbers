"""Google OAuth scaffold — no fake auth.

- GET /api/auth/google/login: redirects to Google's OAuth2 authorize URL when
  GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI are all configured; otherwise 501.
- GET /api/auth/google/callback: exchanges the code via httpx against the
  real Google token endpoint. Failures return 502 with detail; nothing is
  ever faked. On success a signed session cookie is set (see auth_sessions).
"""

import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth_sessions import SESSION_COOKIE_NAME, create_session_value
from ..config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_NOT_CONFIGURED_DETAIL = (
    "Google OAuth not configured. "
    "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_AUTH_REDIRECT_URI."
)


def _oauth_settings():
    return get_settings()


def _is_configured() -> bool:
    s = _oauth_settings()
    return bool(s.GOOGLE_CLIENT_ID and s.GOOGLE_CLIENT_SECRET and s.GOOGLE_AUTH_REDIRECT_URI)


@router.get("/google/login")
def google_login():
    if not _is_configured():
        raise HTTPException(status_code=501, detail=OAUTH_NOT_CONFIGURED_DETAIL)
    s = _oauth_settings()
    params = {
        "client_id": s.GOOGLE_CLIENT_ID,
        "redirect_uri": s.GOOGLE_AUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = GOOGLE_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/google/callback")
async def google_callback(code: Optional[str] = None, error: Optional[str] = None):
    if not _is_configured():
        raise HTTPException(status_code=501, detail=OAUTH_NOT_CONFIGURED_DETAIL)
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    s = _oauth_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": s.GOOGLE_CLIENT_ID,
                    "client_secret": s.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": s.GOOGLE_AUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to complete Google OAuth: {exc}"
        )

    session_value = create_session_value(
        {"sub": userinfo.get("sub"), "email": userinfo.get("email")}
    )
    response = JSONResponse(
        {"status": "authenticated", "email": userinfo.get("email")}
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_value,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response
