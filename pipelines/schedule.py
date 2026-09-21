"""Job roster, cadences, and season helpers for MONEY BY NUMBERS automation.

Phase 10 (spec sections 55-57). Everything in this module is a pure
function of its inputs — no database, no network — so scheduling logic
is fully unit-testable.

Cadence specs (all times UTC):
    {"kind": "daily", "hour_utc": H, "minute_utc": M}
        Runs once per day at H:M.
    {"kind": "weekly_days", "days": [0..6], "hour_utc": H, "minute_utc": M}
        Runs on the given weekdays (Monday=0 .. Sunday=6) at H:M.
    {"kind": "monthly", "day": D, "hour_utc": H, "minute_utc": M}
        Runs on day D of each month at H:M (D is clamped to the last day
        of short months).

"Season" means the NFL season whose regular season starts in September of
year Y, labelled season Y. ``in_nfl_season`` covers the regular season
through the Super Bowl (Sep 1 - Feb 15); jobs flagged ``in_season_only``
skip honestly outside that window.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def current_season(now: Optional[datetime] = None) -> int:
    """The NFL season label for a point in time.

    September-December of year Y -> season Y. January-February of year Y
    -> season Y-1 (the season that started the previous September).
    March-August -> the most recently completed season (Y-1): data for the
    upcoming season may not exist yet, so the daily refresh targets what
    is real rather than guessing at a schedule that is not published.
    """
    now = now or utcnow()
    if now.month >= 9:
        return now.year
    return now.year - 1


def in_nfl_season(now: Optional[datetime] = None) -> bool:
    """True during the NFL season window (Sep 1 through Feb 15)."""
    now = now or utcnow()
    if now.month in (9, 10, 11, 12, 1):
        return True
    return now.month == 2 and now.day <= 15


def last_tick(cadence: Dict[str, Any], now: datetime) -> datetime:
    """The most recent scheduled tick at or before ``now`` (UTC)."""
    kind = cadence["kind"]
    hour = int(cadence.get("hour_utc", 0))
    minute = int(cadence.get("minute_utc", 0))
    if kind == "daily":
        tick = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tick > now:
            tick -= timedelta(days=1)
        return tick
    if kind == "weekly_days":
        days = sorted(int(d) for d in cadence.get("days", []))
        if not days:
            raise ValueError("weekly_days cadence needs at least one day")
        for back in range(0, 8):
            candidate = now - timedelta(days=back)
            tick = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.weekday() in days and tick <= now:
                return tick
        raise AssertionError("unreachable: a matching weekday exists in any 8-day window")
    if kind == "monthly":
        day = int(cadence.get("day", 1))
        for month_offset in (0, 1):
            y, m = now.year, now.month - month_offset
            while m <= 0:
                m += 12
                y -= 1
            last_day = calendar.monthrange(y, m)[1]
            tick = now.replace(year=y, month=m, day=min(day, last_day),
                               hour=hour, minute=minute, second=0, microsecond=0)
            if tick <= now:
                return tick
        raise AssertionError("unreachable")
    raise ValueError(f"unknown cadence kind: {kind!r}")


def next_tick(cadence: Dict[str, Any], now: datetime) -> datetime:
    """The next scheduled tick strictly after ``now`` (UTC)."""
    kind = cadence["kind"]
    hour = int(cadence.get("hour_utc", 0))
    minute = int(cadence.get("minute_utc", 0))

    def at(day: datetime) -> datetime:
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if kind == "daily":
        tick = at(now)
        return tick if tick > now else at(now + timedelta(days=1))
    if kind == "weekly_days":
        days = sorted(int(d) for d in cadence.get("days", []))
        if not days:
            raise ValueError("weekly_days cadence needs at least one day")
        for forward in range(0, 9):
            candidate = at(now + timedelta(days=forward))
            if candidate.weekday() in days and candidate > now:
                return candidate
        raise AssertionError("unreachable: a matching weekday exists in any 9-day window")
    if kind == "monthly":
        day = int(cadence.get("day", 1))
        y, m = now.year, now.month
        for _ in range(3):
            last_day = calendar.monthrange(y, m)[1]
            tick = now.replace(year=y, month=m, day=min(day, last_day),
                               hour=hour, minute=minute, second=0, microsecond=0)
            if tick > now:
                return tick
            m += 1
            if m > 12:
                m = 1
                y += 1
        raise AssertionError("unreachable")
    raise ValueError(f"unknown cadence kind: {kind!r}")


def is_due(cadence: Dict[str, Any], now: datetime,
           last_run_at: Optional[datetime]) -> bool:
    """True when the job has not run since its most recent scheduled tick."""
    if last_run_at is None:
        return True
    tick = last_tick(cadence, now)
    return last_run_at < tick


# ---------------------------------------------------------------------------
# Job roster (spec 56-57)
# ---------------------------------------------------------------------------
# max_gap_hours drives missed-run detection: if the job has no successful
# run within this window (and is in season when required), it is flagged
# as missed. Values allow one missed tick plus operational slack.

JOB_SCHEDULES: List[Dict[str, Any]] = [
    {
        "name": "daily_ingest",
        "job_path": "pipelines.jobs.daily:DailyJob",
        "cadence": {"kind": "daily", "hour_utc": 10, "minute_utc": 0},
        "in_season_only": False,
        "max_gap_hours": 40,
        "description": (
            "Daily NFL data ingestion (nflverse, current season) plus "
            "dataset health check."
        ),
    },
    {
        "name": "odds_pull",
        "job_path": "pipelines.jobs.odds_pull:OddsPullJob",
        "cadence": {"kind": "weekly_days", "days": [1, 4, 6],
                    "hour_utc": 12, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 96,
        "description": (
            "Odds snapshot pull Tue/Fri/Sun in season. Honors the Odds API "
            "credit budget (500/month free tier) and skips honestly when "
            "the key is missing or the budget is exhausted."
        ),
    },
    {
        "name": "postgame_settlement",
        "job_path": "pipelines.jobs.postgame:PostgameJob",
        "cadence": {"kind": "weekly_days", "days": [0, 1],
                    "hour_utc": 13, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 120,
        "description": (
            "Postgame settlement Mon/Tue in season: grades locked picks "
            "against finals (idempotent; never touches the predictions "
            "ledger)."
        ),
    },
    {
        "name": "weekly_picks",
        "job_path": "pipelines.jobs.weekly_picks:WeeklyPicksJob",
        "cadence": {"kind": "weekly_days", "days": [1],
                    "hour_utc": 14, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 120,
        "description": (
            "Weekly picks lock Tue in season: replays the registered "
            "champion Elo through all finals, fits the predicted-margin "
            "slope on 2016-2025, and locks the upcoming week's picks via "
            "the immutable ledger (pre-kickoff only; duplicates refused). "
            "Skips partial weeks — never backfills."
        ),
    },
    {
        "name": "weekly_context",
        "job_path": "pipelines.jobs.weekly:WeeklyJob",
        "cadence": {"kind": "weekly_days", "days": [2],
                    "hour_utc": 11, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 192,
        "description": (
            "Weekly injury report + game weather refresh (nflverse weekly "
            "report, Open-Meteo kickoff weather)."
        ),
    },
    {
        "name": "retrain",
        "job_path": "pipelines.jobs.retrain:RetrainJob",
        "cadence": {"kind": "monthly", "day": 1, "hour_utc": 7, "minute_utc": 0},
        "in_season_only": False,
        "max_gap_hours": 24 * 40,
        "description": (
            "Monthly model retrain gated by the leakage harness. Produces a "
            "CHALLENGER version only — promotion stays manual, always."
        ),
    },
    {
        "name": "recalibrate",
        "job_path": "pipelines.jobs.recalibrate:RecalibrateJob",
        "cadence": {"kind": "weekly_days", "days": [2],
                    "hour_utc": 14, "minute_utc": 0},
        "in_season_only": True,
        "max_gap_hours": 192 + 48,
        "description": (
            "Weekly in-season probability-calibration refit (Platt scaling "
            "on season-to-date settled picks) plus a held-out-guarded "
            "factor review every 4th run. Never promotes a model; never "
            "fabricates data."
        ),
    },
]


def get_schedule(name: str) -> Dict[str, Any]:
    for entry in JOB_SCHEDULES:
        if entry["name"] == name:
            return entry
    raise KeyError(f"unknown scheduled job: {name!r}")


def load_job_class(job_path: str):
    """Import a ``module:Class`` job path from the roster."""
    module_name, class_name = job_path.split(":")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)
