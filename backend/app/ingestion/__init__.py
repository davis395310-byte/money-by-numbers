"""NFL ingestion package: validators + pipeline."""

from .pipeline import ingest_injuries, ingest_weather, run_context_ingestion, run_ingestion
from .validators import (
    injury_warnings,
    is_stale,
    validate_game_record,
    validate_injury_record,
    validate_team_record,
    validate_team_stat_record,
    validate_weather_record,
)

__all__ = [
    "ingest_injuries",
    "ingest_weather",
    "injury_warnings",
    "is_stale",
    "run_context_ingestion",
    "run_ingestion",
    "validate_game_record",
    "validate_injury_record",
    "validate_team_record",
    "validate_team_stat_record",
    "validate_weather_record",
]
