"""Odds providers (Phase 4): The Odds API v4.

The free tier grants 500 credits/month; a call to the NFL odds endpoint
costs ~1 credit. Credit budget design:

- Every odds response carries ``x-requests-used`` / ``x-requests-remaining``
  headers. After each fetch the provider persists them to the
  ``odds_credit_usage`` collection as {month, used, remaining, last_updated}.
- ``check_budget(db, min_required=1)`` reads that ledger and returns False
  with an honest reason when remaining < min_required, or when the balance
  is unknown (no ledger record for the current month — e.g. a brand-new
  key). The ingestion script refuses to pull on a False budget (exit 2),
  so a run can never silently burn credits it does not have.
- Planned refresh cadence: Tuesday / Friday / Sunday mornings in season,
  ~12-14 pulls/month — far under the 500-credit free tier even if a pull
  ever costs more than 1 credit. Odds are never cached on disk: a cache
  hit would serve stale lines and decouple credit headers from real usage.

Auth: ``OddsProvider.requires_auth`` is True. ``auth_configured()`` is True
only when ``ODDS_API_KEY`` is set and non-empty. The key is passed as a
query param to the curl call and is never logged — logs and errors only
ever show a redacted URL.

The provider never fabricates lines: unmatched team names are logged and
skipped (never guessed), missing markets stay None, and failures return
recorded errors instead of synthesized records.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings

from .base import ProviderResult, RateLimiter, retry_with_backoff, utcnow
from .teams_data import TEAMS

log = logging.getLogger("mnb.providers.odds")

DEFAULT_BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"
ODDS_PATH = "/sports/americanfootball_nfl/odds"

# Free tier: 500 credits/month, ~1 credit per NFL odds call.
FREE_TIER_MONTHLY_CREDITS = 500

# Full team name ("Kansas City Chiefs") -> canonical abbr. Events whose
# home/away names are not in this map are logged and skipped — never guessed.
TEAM_FULL_NAME_TO_ABBR: Dict[str, str] = {
    f"{t['city']} {t['name']}".lower(): t["abbr"] for t in TEAMS
}

# Matching window: an event matches a games-collection game with the same
# home/away pair whose game_date is within 10 days of commence_time.
GAME_MATCH_WINDOW = timedelta(days=10)

_MISSING = object()


def _parse_iso(text: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; None when missing or unparseable."""
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redacted_url(url: str) -> str:
    """Mask the apiKey query param for logs/errors (key is never logged)."""
    parts = url.split("apiKey=", 1)
    if len(parts) == 2:
        tail = parts[1]
        rest = tail[tail.find("&"):] if "&" in tail else ""
        return parts[0] + "apiKey=<redacted>" + rest
    return url


class OddsProvider(ABC):
    """Contract for odds providers (sibling of NFLDataProvider).

    ``requires_auth`` is True; ``auth_configured()`` is True only when the
    provider's API key is set and non-empty. Subclasses must never fabricate
    records — missing values stay None and are reported, never invented.
    """

    name: str = "base"
    requires_auth: bool = True

    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        # No DiskCache for live odds: a cached response would serve stale
        # lines and decouple the credit headers from real API usage.
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=1.0)
        self.last_errors: List[str] = []
        self._last_fetched_at: Optional[datetime] = None

    # -- identity / auth ----------------------------------------------------
    def auth_configured(self) -> bool:
        """True only when the provider has a usable API key."""
        return not self.requires_auth

    # -- freshness ----------------------------------------------------------
    @property
    def last_fetched_at(self) -> Optional[datetime]:
        return self._last_fetched_at

    def is_fresh(self, ttl_hours: float = 24.0) -> bool:
        if self._last_fetched_at is None:
            return False
        age = (utcnow() - self._last_fetched_at).total_seconds() / 3600.0
        return age <= ttl_hours

    # -- fetches ------------------------------------------------------------
    @abstractmethod
    def fetch_odds(
        self,
        seasons: Optional[List[int]] = None,
        weeks: Optional[List[int]] = None,
        db: Any = None,
    ) -> ProviderResult:
        """Fetch current odds snapshots.

        ``seasons``/``weeks`` are informational only — The Odds API odds
        endpoint returns upcoming games and is not filterable by
        season/week, so records carry ``season=None``/``week=None`` rather
        than invented values. ``db`` (optional) is used to match events to
        the ``games`` collection; without it, ``game_id`` falls back to a
        derived ``odds:<away>:<home>:<date>`` key (logged).

        Normalized record shape:
            {game_id, season, week, timestamp, sportsbook, spread,
             moneyline_home, moneyline_away, total}
        Missing values stay None.
        """
        ...

    # -- helpers ------------------------------------------------------------
    def _record_success(self) -> None:
        self._last_fetched_at = utcnow()
        self.last_errors = []

    def _record_errors(self, errors: List[str]) -> None:
        self.last_errors = list(errors)


class TheOddsApiProvider(OddsProvider):
    """The Odds API v4 provider (https://the-odds-api.com).

    Endpoint: GET {base}/sports/americanfootball_nfl/odds
              ?apiKey={key}&regions=us&markets=h2h,spreads,totals&oddsFormat=american
    """

    name = "theoddsapi"
    requires_auth = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        http_timeout: float = 30.0,
    ) -> None:
        super().__init__(rate_limiter=rate_limiter)
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.ODDS_API_KEY
        base = base_url or settings.ODDS_API_BASE_URL or DEFAULT_BASE_URL
        self.base_url = base.rstrip("/")
        self.http_timeout = http_timeout
        self.last_credit_used: Optional[int] = None
        self.last_credit_remaining: Optional[int] = None

    # -- auth ---------------------------------------------------------------
    def auth_configured(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip())

    # -- transport ----------------------------------------------------------
    def _odds_url(self) -> str:
        return (
            f"{self.base_url}{ODDS_PATH}"
            f"?apiKey={self.api_key}"
            f"&regions=us&markets=h2h,spreads,totals&oddsFormat=american"
        )

    @retry_with_backoff(
        tries=3,
        base_delay=2.0,
        max_delay=30.0,
        retryable=lambda exc: not isinstance(exc, _HttpError),
    )
    def _request(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """GET the odds endpoint via curl; returns (events, response headers).

        Uses the ``curl`` binary via subprocess (same reason as nflverse:
        Python HTTP clients stall behind this runtime's egress proxy).
        Response headers are captured with ``-D`` so credit usage can be
        tracked. Retries network/timeout failures but NOT HTTP errors
        (exit 22): a 401/429 must not be retried, since retrying cannot fix
        a bad key and could waste credits.
        """
        url = self._odds_url()
        max_time = str(max(1, int(self.http_timeout)))
        with tempfile.TemporaryDirectory(prefix="mnb_odds_") as tmpdir:
            headers_path = os.path.join(tmpdir, "headers.txt")
            body_path = os.path.join(tmpdir, "body.json")
            self.rate_limiter.wait()
            log.info("fetching %s", _redacted_url(url))
            proc = subprocess.run(
                ["curl", "-sS", "--fail", "--max-time", max_time,
                 "-D", headers_path, "-o", body_path, url],
                capture_output=True,
                timeout=self.http_timeout + 60,
            )
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", "replace").strip()[:300]
                # Exit 22 = curl --fail on HTTP >= 400 (bad key, quota
                # exhausted, etc.): honest, non-retryable failure.
                if proc.returncode == 22:
                    raise _HttpError(
                        "The Odds API returned an HTTP error "
                        f"(curl exit 22; detail: {stderr or 'none'})"
                    )
                raise RuntimeError(
                    f"curl exit {proc.returncode}: {stderr or 'no detail'}"
                )
            raw = open(body_path, "rb").read()
            if not raw:
                raise RuntimeError("The Odds API returned an empty body")
            try:
                events = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"odds response was not valid JSON: {exc}")
            if not isinstance(events, list):
                raise RuntimeError(
                    f"unexpected odds response shape: {type(events).__name__}, expected list"
                )
            headers = self._parse_headers(headers_path)
            return events, headers

    @staticmethod
    def _parse_headers(headers_path: str) -> Dict[str, str]:
        """Parse captured response headers into a lowercase-keyed dict."""
        headers: Dict[str, str] = {}
        try:
            text = open(headers_path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            log.warning("could not read captured response headers: %s", exc)
            return headers
        for line in text.splitlines():
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            name = name.strip().lower()
            if name and name not in headers:
                headers[name] = value.strip()
        return headers

    # -- fetch --------------------------------------------------------------
    def fetch_odds(
        self,
        seasons: Optional[List[int]] = None,
        weeks: Optional[List[int]] = None,
        db: Any = None,
    ) -> ProviderResult:
        if not self.auth_configured():
            msg = "ODDS_API_KEY is not configured; refusing to fetch odds"
            self._record_errors([msg])
            return ProviderResult(source=self.name, errors=[msg])
        if seasons or weeks:
            log.info(
                "seasons=%s weeks=%s noted but not sent: the odds endpoint "
                "returns upcoming games and is not filterable by season/week",
                seasons, weeks,
            )
        try:
            events, headers = self._request()
        except Exception as exc:  # noqa: BLE001 - honest failure, never fabricated
            msg = f"odds fetch failed: {exc}"
            self._record_errors([msg])
            return ProviderResult(source=self.name, errors=[msg])

        self.last_credit_used = _opt_int(headers.get("x-requests-used"))
        self.last_credit_remaining = _opt_int(headers.get("x-requests-remaining"))
        if self.last_credit_remaining is None:
            log.warning(
                "odds response carried no x-requests-remaining header; "
                "credit balance unknown for this fetch"
            )
        else:
            log.info(
                "odds fetch OK: %d events; credits used=%s remaining=%s",
                len(events), self.last_credit_used, self.last_credit_remaining,
            )

        records, errors = self.parse_odds_payload(events, db=db)
        if records:
            self._record_success()
        else:
            self._record_errors(errors or ["no odds records parsed from response"])
        return ProviderResult(source=self.name, records=records, errors=errors)

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def _lookup_abbr(full_name: Any) -> Optional[str]:
        if not full_name:
            return None
        return TEAM_FULL_NAME_TO_ABBR.get(str(full_name).strip().lower())

    @staticmethod
    def _match_game_id(
        db: Any,
        home_abbr: str,
        away_abbr: str,
        commence: Optional[datetime],
    ) -> Tuple[str, bool]:
        """Match an event to the games collection.

        Returns (game_id, matched). Matches on (home_team, away_team) with
        game_date within 10 days of commence_time; closest date wins. Falls
        back to ``odds:<home>:<away>:<date>`` when no DB is available or no
        game matches — the fallback is returned with matched=False so the
        caller logs it.
        """
        commence_date = commence.date().isoformat() if commence else "unknown"
        fallback = f"odds:{home_abbr}:{away_abbr}:{commence_date}"
        if db is None or commence is None:
            return fallback, False
        try:
            candidates = list(
                db["games"].find(
                    {
                        "home_team": home_abbr,
                        "away_team": away_abbr,
                        "game_date": {
                            "$gte": commence - GAME_MATCH_WINDOW,
                            "$lte": commence + GAME_MATCH_WINDOW,
                        },
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 - DB trouble must not kill parsing
            log.warning("game lookup failed, using fallback game_id: %s", exc)
            return fallback, False
        if not candidates:
            return fallback, False
        best = min(
            candidates,
            key=lambda g: abs(
                (_parse_iso(g.get("game_date")) or commence) - commence
            ),
        )
        return str(best.get("game_id", fallback)), True

    @staticmethod
    def _extract_markets(
        bookmaker: Dict[str, Any], home_name: str, away_name: str
    ) -> Dict[str, Optional[float]]:
        """Pull h2h/spreads/totals values from one bookmaker's markets.

        - h2h: moneyline_home / moneyline_away (american odds ints).
        - spreads: home spread point, taken from the outcome named for the
          home team; when both teams' outcomes are present their points are
          cross-checked (away should be -home) and a mismatch is warned on.
        - totals: the Over point; cross-checked against the Under point.
        Missing markets stay None.
        """
        moneyline_home: Optional[int] = None
        moneyline_away: Optional[int] = None
        spread: Optional[float] = None
        total: Optional[float] = None

        for market in bookmaker.get("markets") or []:
            key = market.get("key")
            outcomes = market.get("outcomes") or []
            if key == "h2h":
                for outcome in outcomes:
                    name = outcome.get("name")
                    price = _opt_int(outcome.get("price"))
                    if price is None or not name:
                        continue
                    if name == home_name:
                        moneyline_home = price
                    elif name == away_name:
                        moneyline_away = price
                    else:
                        log.warning(
                            "h2h outcome name %r matches neither %r nor %r; skipping",
                            name, home_name, away_name,
                        )
            elif key == "spreads":
                home_point: Any = _MISSING
                away_point: Any = _MISSING
                for outcome in outcomes:
                    name = outcome.get("name")
                    point = outcome.get("point")
                    if name == home_name:
                        home_point = point
                    elif name == away_name:
                        away_point = point
                spread = _opt_float(None if home_point is _MISSING else home_point)
                if (
                    home_point is not _MISSING
                    and away_point is not _MISSING
                    and home_point is not None
                    and away_point is not None
                ):
                    try:
                        if abs(float(home_point) + float(away_point)) > 0.001:
                            log.warning(
                                "spread sign convention mismatch for %s vs %s: "
                                "home point %s, away point %s",
                                home_name, away_name, home_point, away_point,
                            )
                    except (TypeError, ValueError):
                        pass
            elif key == "totals":
                over_point: Any = _MISSING
                under_point: Any = _MISSING
                for outcome in outcomes:
                    name = str(outcome.get("name") or "").strip().lower()
                    if name == "over":
                        over_point = outcome.get("point")
                    elif name == "under":
                        under_point = outcome.get("point")
                total = _opt_float(None if over_point is _MISSING else over_point)
                if (
                    over_point is not _MISSING
                    and under_point is not _MISSING
                    and over_point is not None
                    and under_point is not None
                    and over_point != under_point
                ):
                    log.warning(
                        "totals Over/Under points disagree (%s vs %s); using Over",
                        over_point, under_point,
                    )
        return {
            "moneyline_home": moneyline_home,
            "moneyline_away": moneyline_away,
            "spread": spread,
            "total": total,
        }

    @staticmethod
    def parse_odds_payload(
        events: List[Dict[str, Any]], db: Any = None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Normalize a raw odds payload into snapshot records.

        Static so it can be unit-tested (and dry-run) without network or
        API key. One record per (event, bookmaker). ``db`` enables game_id
        matching against the games collection; without it the derived
        ``odds:<home>:<away>:<date>`` fallback is used.
        """
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        for event in events:
            event_id = event.get("id", "?")
            home_name = event.get("home_team")
            away_name = event.get("away_team")
            home_abbr = TheOddsApiProvider._lookup_abbr(home_name)
            away_abbr = TheOddsApiProvider._lookup_abbr(away_name)
            if home_abbr is None or away_abbr is None:
                errors.append(
                    f"event {event_id}: unrecognized team name(s) "
                    f"home={home_name!r} away={away_name!r}; event skipped"
                )
                continue
            commence = _parse_iso(event.get("commence_time"))
            game_id, matched = TheOddsApiProvider._match_game_id(
                db, home_abbr, away_abbr, commence
            )
            if not matched:
                errors.append(
                    f"event {event_id}: no games-collection match for "
                    f"{away_abbr} @ {home_abbr}; using fallback game_id {game_id}"
                )
            bookmakers = event.get("bookmakers") or []
            if not bookmakers:
                errors.append(f"event {event_id}: no bookmakers in response; skipped")
                continue
            for bookmaker in bookmakers:
                sportsbook = bookmaker.get("key")
                timestamp = _parse_iso(bookmaker.get("last_update"))
                if not sportsbook or timestamp is None:
                    errors.append(
                        f"event {event_id}: bookmaker missing key/last_update; skipped"
                    )
                    continue
                markets = TheOddsApiProvider._extract_markets(
                    bookmaker, home_name, away_name
                )
                records.append(
                    {
                        "game_id": game_id,
                        "season": None,  # not provided by the endpoint; never invented
                        "week": None,    # not provided by the endpoint; never invented
                        "timestamp": timestamp,
                        "sportsbook": sportsbook,
                        "spread": markets["spread"],
                        "moneyline_home": markets["moneyline_home"],
                        "moneyline_away": markets["moneyline_away"],
                        "total": markets["total"],
                    }
                )
        return records, errors

    # -- credit ledger ------------------------------------------------------
    def record_credit_usage(self, db: Any) -> None:
        """Upsert this month's credit usage doc in ``odds_credit_usage``.

        Called after a fetch; a no-op (with a warning) when the response
        carried no credit headers or no DB is available.
        """
        if db is None:
            log.warning("no database available; credit usage not persisted")
            return
        if self.last_credit_used is None and self.last_credit_remaining is None:
            log.warning(
                "no credit headers captured; skipping odds_credit_usage update"
            )
            return
        month = utcnow().strftime("%Y-%m")
        db["odds_credit_usage"].update_one(
            {"month": month},
            {
                "$set": {
                    "used": self.last_credit_used,
                    "remaining": self.last_credit_remaining,
                    "last_updated": utcnow(),
                }
            },
            upsert=True,
        )
        log.info(
            "odds credit ledger updated for %s: used=%s remaining=%s",
            month, self.last_credit_used, self.last_credit_remaining,
        )


class _HttpError(RuntimeError):
    """Non-retryable HTTP failure from The Odds API (bad key, quota, ...)."""


def check_budget(db: Any, min_required: int = 1) -> Tuple[bool, str]:
    """Check the odds credit budget before pulling.

    Returns (ok, reason). False with an honest reason when:
    - no ``odds_credit_usage`` record exists for the current month (balance
      unknown — e.g. a brand-new key), or
    - the recorded remaining credits are unknown or below ``min_required``.

    A False budget means "do not pull" — the ingestion script exits 2.
    """
    month = utcnow().strftime("%Y-%m")
    doc = None
    try:
        doc = db["odds_credit_usage"].find_one({"month": month})
    except Exception as exc:  # noqa: BLE001
        return False, f"could not read odds_credit_usage ledger: {exc}"
    if doc is None:
        return (
            False,
            f"no credit-usage record for {month}: remaining balance unknown "
            f"(brand-new key or never fetched). Make one manual call and "
            f"record the balance, or re-run with --allow-unknown-budget.",
        )
    remaining = doc.get("remaining")
    if remaining is None:
        return (
            False,
            f"credit balance unknown (last fetch for {month} returned no "
            f"x-requests-remaining header). Refusing to pull blind.",
        )
    if int(remaining) < min_required:
        return (
            False,
            f"only {remaining} odds API credits remaining for {month}; "
            f"need at least {min_required}. Refusing to pull.",
        )
    return True, f"{remaining} odds API credits remaining for {month} (need {min_required})"
