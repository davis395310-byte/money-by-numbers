"""Weekly context job (Phase 10) — spec sections 39 and 57.

Runs weekly during the NFL season (Wednesday):

- Refreshes the official weekly injury report (nflverse, free, no key).
- Refreshes kickoff weather for the current season's games (Open-Meteo,
  free, no key).

Both providers are honest about coverage: the injury feed is the weekly
report (not a live wire), and games beyond the ~16-day forecast horizon
are skipped with a recorded reason — never backfilled. Upserts are
idempotent, so re-running changes nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pipelines.jobs._common import require_db, utcnow
from pipelines.schedule import current_season
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

GAME_PROJECTION = {
    "_id": 0,
    "game_id": 1,
    "venue": 1,
    "game_date": 1,
    "roof": 1,
    "home_team": 1,
    "away_team": 1,
    "season": 1,
    "week": 1,
}


def _default_context_ingest(db: Any, season: int, weeks: List[int]) -> Dict[str, Any]:
    """Real refresh: injuries + weather via the Phase 5 pipeline."""
    from pipelines.jobs._common import ensure_backend

    ensure_backend()
    from app.data_providers import NflverseInjuryProvider, OpenMeteoWeatherProvider
    from app.data_providers.nflverse import NflverseProvider
    from app.ingestion.pipeline import run_context_ingestion

    # Games for weather: prefer the stored schedule, fall back to the
    # nflverse provider so the job works on a fresh database.
    games: List[Dict[str, Any]] = []
    stored = list(
        db["games"].find(
            {"season": season, "week": {"$in": weeks}}, GAME_PROJECTION
        )
    )
    for doc in stored:
        games.append(
            {
                "game_id": doc.get("game_id"),
                "venue": doc.get("venue"),
                "kickoff": doc.get("game_date"),
                "roof": doc.get("roof"),
            }
        )
    if not games:
        result = NflverseProvider().fetch_games([season])
        for record in result.records:
            if record.get("week") not in set(weeks):
                continue
            games.append(
                {
                    "game_id": record.get("game_id"),
                    "venue": record.get("venue"),
                    "kickoff": record.get("game_date"),
                    "roof": record.get("roof"),
                }
            )

    injury_provider = NflverseInjuryProvider()
    weather_provider = OpenMeteoWeatherProvider()
    return run_context_ingestion(
        db, injury_provider, weather_provider, [season], games
    )


class WeeklyJob(Job):
    """Weekly injury + weather refresh."""

    name = "weekly_context"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        now = context.get("now") or utcnow()
        season = current_season(now)
        weeks = list(range(1, 23))
        context_fn = context.get("context_fn") or _default_context_ingest

        try:
            summary = context_fn(db, season, weeks)
        except SkipJob:
            raise
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED,
                          error=f"context refresh failed: {exc}"[:500])
            return record

        status = (summary or {}).get("status")
        record.detail = {
            "season": season,
            "ingestion_status": status,
            "records_received": (summary or {}).get("records_received"),
            "records_accepted": (summary or {}).get("records_accepted"),
            "records_rejected": (summary or {}).get("records_rejected"),
        }
        if status in ("success", "partial"):
            record.finish(JobStatus.SUCCEEDED)
        else:
            errors = (summary or {}).get("errors") or []
            record.finish(
                JobStatus.FAILED,
                error=f"context refresh status={status}: {str(errors[:2])[:400]}",
            )
        return record
