"""Game-venue weather provider (product spec section 33).

Source: Open-Meteo (https://open-meteo.com) — free for non-commercial use,
no API key, no signup, data under CC BY 4.0 (attribution required — the API
docs carry the attribution notice). Endpoints used:

- geocoding: https://geocoding-api.open-meteo.com/v1/search
- forecast:  https://api.open-meteo.com/v1/forecast   (up to 16 days ahead)
- archive:   https://archive-api.open-meteo.com/v1/archive (ERA5, 1940+)

For each game the provider resolves the venue name to coordinates — first
via the curated stadium table (``app/data_providers/stadiums.py``), then
the geocoding API (long-TTL disk cache) as fallback — then pulls hourly
data and selects the hour nearest kickoff. Temperatures are requested in
Fahrenheit, wind in mph, precipitation in inches.

HONEST LIMITS (documented, never hidden):
- kickoff more than 16 days in the future: skipped as "beyond forecast
  range" — no forecast is invented.
- kickoff within the last ~5 days: the forecast API still covers it
  (``past_days``); older games use the ERA5 archive (a few days of lag).
- venue that doesn't geocode: skipped, error recorded, no coordinates
  guessed.
- dome/closed-roof games: the record is marked ``dome=True`` so consumers
  know outdoor weather is irrelevant. Roof status comes from the game
  record when available; otherwise ``dome`` is None (unknown, not False).

No authentication required: ``WEATHER_API_KEY`` is not needed with this
provider and is ignored if set (kept in .env.example only for a future
paid tier).
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    DiskCache,
    ProviderResult,
    RateLimiter,
    default_cache_dir as _default_cache_dir,
    retry_with_backoff,
    utcnow,
)
from .stadiums import lookup_stadium

log = logging.getLogger("mnb.providers.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

FORECAST_RANGE_DAYS = 16  # Open-Meteo forecast horizon
ARCHIVE_LAG_DAYS = 5  # use archive (not forecast+past_days) for older games

HOURLY_VARS = (
    "temperature_2m,precipitation_probability,precipitation,"
    "weathercode,windspeed_10m,windgusts_10m"
)

#: WMO weather-code interpretation (subset used by Open-Meteo).
WMO_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def describe_weathercode(code: Any) -> Optional[str]:
    """Human description for a WMO weather code; None when unknown."""
    if code is None:
        return None
    try:
        return WMO_DESCRIPTIONS.get(int(code))
    except (TypeError, ValueError):
        return None


def classify_roof(roof: Any) -> Optional[bool]:
    """Map a roof description to dome True/False/None (unknown).

    "retractable" is unknown at fetch time — reported as None, never
    guessed. Anything unrecognized is also None.
    """
    if roof is None:
        return None
    text = str(roof).strip().lower()
    if text in ("dome", "closed"):
        return True
    if text in ("outdoors", "open"):
        return False
    return None


def _as_aware_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _nearest_hour_index(times: List[str], kickoff: datetime) -> Optional[int]:
    """Index of the hourly timestamp nearest to kickoff."""
    best: Optional[int] = None
    best_gap: Optional[float] = None
    for i, stamp in enumerate(times):
        try:
            naive = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        aware = naive.replace(tzinfo=timezone.utc)
        gap = abs((aware - kickoff).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best = i
    return best


class OpenMeteoWeatherProvider:
    """Fetches kickoff-hour weather for game venues from Open-Meteo.

    Input game dicts: ``{game_id, venue|None, kickoff (datetime|iso)|None,
    roof|None}``. Output records::

        {game_id, venue, latitude, longitude, kickoff (iso),
         temp_f, wind_mph, wind_gust_mph, precip_prob (0-1|None),
         precip_in, weathercode, conditions, dome (bool|None),
         source, fetched_at (iso)}
    """

    name = "open-meteo"
    requires_auth = False

    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
        http_timeout: float = 60.0,
    ) -> None:
        self.cache = cache or DiskCache(_default_cache_dir() / self.name)
        # Fair use: be gentle — one request per 1.2s is far under limits.
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=1.2)
        self.geo_cache = DiskCache(_default_cache_dir() / (self.name + "-geo"), default_ttl_hours=24 * 90)
        self.last_errors: List[str] = []
        self._last_fetched_at: Optional[datetime] = None
        self.http_timeout = http_timeout

    @property
    def last_fetched_at(self) -> Optional[datetime]:
        return self._last_fetched_at

    def is_fresh(self, ttl_hours: float = 24.0) -> bool:
        if self._last_fetched_at is None:
            return False
        age = (utcnow() - self._last_fetched_at).total_seconds() / 3600.0
        return age <= ttl_hours

    def auth_configured(self) -> bool:
        # Open-Meteo needs no key at all.
        return True

    # -- transport (curl, same proxy-safe pattern as the other providers) ----
    @retry_with_backoff(tries=3, base_delay=2.0, max_delay=30.0)
    def _get_json(self, url: str, params: Dict[str, Any], cache: Optional[DiskCache] = None) -> Dict[str, Any]:
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"
        store = cache if cache is not None else self.cache
        cached = store.get(full_url)
        if cached is not None:
            return json.loads(cached.decode("utf-8"))
        self.rate_limiter.wait()
        log.info("GET %s (params redacted in log)", url)
        max_time = str(max(1, int(self.http_timeout)))
        proc = subprocess.run(
            ["curl", "-sSL", "--fail", "--max-time", max_time, full_url],
            capture_output=True,
            timeout=self.http_timeout + 60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:300]}"
            )
        payload = proc.stdout
        if not payload:
            raise RuntimeError("curl returned an empty body")
        store.put(full_url, payload)
        return json.loads(payload.decode("utf-8"))

    # -- venue resolution ----------------------------------------------------
    def resolve_venue(self, venue: str) -> Optional[Tuple[float, float, str]]:
        """Resolve a venue name to (lat, lon, matched_name).

        Order: (1) the curated stadium table (exact, no network);
        (2) the Open-Meteo geocoding API (cached 90 days); None on miss.
        Coordinates are never guessed.
        """
        table = lookup_stadium(venue)
        if table is not None:
            return table[0], table[1], venue.strip()
        geo = self.geocode_venue(venue)
        if geo is not None:
            log.info("venue %r resolved via geocoding API -> %s", venue, geo[2])
        return geo

    def geocode_venue(self, venue: str) -> Optional[Tuple[float, float, str]]:
        """Resolve a venue name to (lat, lon, matched_name). None on miss."""
        try:
            data = self._get_json(
                GEOCODE_URL,
                {"name": venue, "count": 1, "language": "en", "format": "json"},
                cache=self.geo_cache,
            )
        except Exception as exc:  # noqa: BLE001 - miss, not a crash
            log.warning("geocode failed for %r: %s", venue, exc)
            return None
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        try:
            return float(top["latitude"]), float(top["longitude"]), str(top.get("name", venue))
        except (KeyError, TypeError, ValueError):
            return None

    # -- weather fetch -------------------------------------------------------
    def _fetch_hourly(
        self, lat: float, lon: float, kickoff: datetime
    ) -> Optional[Dict[str, Any]]:
        """Fetch hourly data and return the hour nearest kickoff.

        Returns None when kickoff is beyond the forecast range (honest
        skip, decided by the caller) or the API call fails.
        """
        now = utcnow()
        if kickoff > now + timedelta(days=FORECAST_RANGE_DAYS):
            return None  # beyond forecast range — caller records the reason
        if kickoff < now - timedelta(days=ARCHIVE_LAG_DAYS):
            day = kickoff.date().isoformat()
            data = self._get_json(
                ARCHIVE_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": day,
                    "end_date": day,
                    "hourly": HOURLY_VARS,
                    "temperature_unit": "fahrenheit",
                    "windspeed_unit": "mph",
                    "precipitation_unit": "inch",
                    "timezone": "auto",
                },
            )
        else:
            data = self._get_json(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": HOURLY_VARS,
                    "temperature_unit": "fahrenheit",
                    "windspeed_unit": "mph",
                    "precipitation_unit": "inch",
                    "timezone": "auto",
                    "forecast_days": FORECAST_RANGE_DAYS,
                    "past_days": ARCHIVE_LAG_DAYS,
                },
            )
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        idx = _nearest_hour_index(times, kickoff)
        if idx is None:
            return None

        def val(key: str) -> Optional[float]:
            series = hourly.get(key) or []
            if idx >= len(series):
                return None
            raw = series[idx]
            return None if raw is None else float(raw)

        precip_prob = val("precipitation_probability")
        return {
            "temp_f": val("temperature_2m"),
            "wind_mph": val("windspeed_10m"),
            "wind_gust_mph": val("windgusts_10m"),
            "precip_prob": (precip_prob / 100.0) if precip_prob is not None else None,
            "precip_in": val("precipitation"),
            "weathercode": val("weathercode"),
        }

    def fetch_game_weather(self, games: List[Dict[str, Any]]) -> ProviderResult:
        """Fetch kickoff-hour weather for the given games."""
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        for game in games:
            game_id = game.get("game_id") or "unknown"
            venue = (game.get("venue") or "").strip()
            kickoff = _as_aware_utc(game.get("kickoff") or game.get("game_date"))
            if not venue:
                errors.append(f"game {game_id}: no venue — weather skipped")
                continue
            if kickoff is None:
                errors.append(f"game {game_id}: no kickoff time — weather skipped")
                continue
            if kickoff > utcnow() + timedelta(days=FORECAST_RANGE_DAYS):
                errors.append(
                    f"game {game_id}: kickoff beyond {FORECAST_RANGE_DAYS}-day "
                    "forecast range — weather skipped (not invented)"
                )
                continue
            geo = self.resolve_venue(venue)
            if geo is None:
                errors.append(f"game {game_id}: venue {venue!r} could not be located — skipped")
                continue
            lat, lon, matched = geo
            try:
                hour = self._fetch_hourly(lat, lon, kickoff)
            except Exception as exc:  # noqa: BLE001 - one bad game must not kill the batch
                errors.append(f"game {game_id}: weather fetch failed: {exc}")
                continue
            if hour is None:
                errors.append(f"game {game_id}: no hourly data near kickoff — skipped")
                continue
            code = hour.get("weathercode")
            code_int = int(code) if code is not None else None
            records.append(
                {
                    "game_id": game_id,
                    "venue": venue,
                    "venue_matched": matched,
                    "latitude": lat,
                    "longitude": lon,
                    "kickoff": kickoff.isoformat(),
                    "temp_f": hour.get("temp_f"),
                    "wind_mph": hour.get("wind_mph"),
                    "wind_gust_mph": hour.get("wind_gust_mph"),
                    "precip_prob": hour.get("precip_prob"),
                    "precip_in": hour.get("precip_in"),
                    "weathercode": code_int,
                    "conditions": describe_weathercode(code_int),
                    "dome": classify_roof(game.get("roof")),
                    "source": "open-meteo",
                    "fetched_at": utcnow().isoformat(),
                }
            )
        result = ProviderResult(
            source=self.name, records=records, fetched_at=utcnow(), errors=errors
        )
        self.last_errors = list(errors)
        self._last_fetched_at = result.fetched_at
        return result

    def coverage(self) -> Dict[str, Any]:
        return {
            "source": "Open-Meteo (https://open-meteo.com), free non-commercial, no API key",
            "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
            "forecast_horizon_days": FORECAST_RANGE_DAYS,
            "archive_from": "1940-01-01",
            "units": {"temp": "F", "wind": "mph", "precipitation": "inch"},
            "note": (
                "Kickoff-hour conditions at the venue. Dome/closed-roof games "
                "are flagged so consumers can ignore outdoor weather."
            ),
        }
