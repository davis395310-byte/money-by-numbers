"""MongoDB client factory and index management.

- get_mongo_client() returns a pymongo.MongoClient when MONGODB_URI is set,
  otherwise None (caller must report "not_configured").
- ensure_indexes(db) creates every collection index defined in INDEX_SPECS.
  It works against both a real pymongo database and mongomock in tests.
"""

from typing import Any, Dict, List, Optional, Tuple

from pymongo import ASCENDING, DESCENDING, MongoClient

from .config import get_settings


def get_mongo_client() -> Optional[MongoClient]:
    """Return a MongoClient if MONGODB_URI is configured, else None."""
    uri = get_settings().MONGODB_URI
    if not uri:
        return None
    return MongoClient(uri, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)


def get_database_name() -> str:
    """Resolve the database name: explicit setting, URI path, or default."""
    settings = get_settings()
    if settings.MONGODB_DB:
        return settings.MONGODB_DB
    if settings.MONGODB_URI:
        try:
            return MongoClient(settings.MONGODB_URI).get_default_database().name
        except Exception:
            pass
    return "money_by_numbers"


# (collection, keys, kwargs) for every collection in the platform.
INDEX_SPECS: List[Tuple[str, List[Tuple[str, Any]], Dict[str, Any]]] = [
    ("teams", [("abbr", ASCENDING)], {"unique": True, "name": "teams_abbr_unique"}),
    ("players", [("player_id", ASCENDING)], {"unique": True, "name": "players_player_id_unique"}),
    ("players", [("team", ASCENDING)], {"name": "players_team"}),
    (
        "games",
        [("season", ASCENDING), ("week", ASCENDING), ("home_team", ASCENDING), ("away_team", ASCENDING)],
        {"unique": True, "name": "games_season_week_teams_unique"},
    ),
    ("games", [("game_id", ASCENDING)], {"unique": True, "name": "games_game_id_unique"}),
    (
        "game_stats",
        [("game_id", ASCENDING), ("team", ASCENDING)],
        {"unique": True, "name": "game_stats_game_team_unique"},
    ),
    (
        "injuries",
        [("season", ASCENDING), ("week", ASCENDING), ("gsis_id", ASCENDING)],
        {"unique": True, "name": "injuries_season_week_player_unique"},
    ),
    (
        "injuries",
        [("season", ASCENDING), ("week", ASCENDING), ("team", ASCENDING)],
        {"name": "injuries_season_week_team"},
    ),
    ("weather", [("game_id", ASCENDING)], {"unique": True, "name": "weather_game_id_unique"}),
    ("odds", [("game_id", ASCENDING), ("timestamp", ASCENDING)], {"name": "odds_game_timestamp"}),
    ("odds", [("sportsbook", ASCENDING)], {"name": "odds_sportsbook"}),
    (
        "odds_credit_usage",
        [("month", ASCENDING)],
        {"unique": True, "name": "odds_credit_usage_month_unique"},
    ),
    (
        "predictions",
        [("prediction_id", ASCENDING)],
        {"unique": True, "name": "predictions_prediction_id_unique"},
    ),
    (
        "predictions",
        [("game_id", ASCENDING), ("model_version", ASCENDING)],
        {"name": "predictions_game_model_version"},
    ),
    (
        # One published pick per (game, model, market): the ledger writer
        # refuses duplicates, and this index makes it structural.
        "predictions",
        [("game_id", ASCENDING), ("model_version", ASCENDING), ("market", ASCENDING)],
        {"unique": True, "name": "predictions_game_model_market_unique"},
    ),
    (
        # Settlement results: exactly one row per locked pick. Re-running
        # settlement upserts by prediction_id — never double-counts.
        "pick_results",
        [("prediction_id", ASCENDING)],
        {"unique": True, "name": "pick_results_prediction_id_unique"},
    ),
    (
        "pick_results",
        [("season", ASCENDING), ("week", ASCENDING)],
        {"name": "pick_results_season_week"},
    ),
    (
        "pick_results",
        [("model_version", ASCENDING)],
        {"name": "pick_results_model_version"},
    ),
    (
        "pick_results",
        [("confidence", ASCENDING)],
        {"name": "pick_results_confidence"},
    ),
    (
        "settlement_runs",
        [("run_id", ASCENDING)],
        {"unique": True, "name": "settlement_runs_run_id_unique"},
    ),
    (
        "model_versions",
        [("version", ASCENDING)],
        {"unique": True, "name": "model_versions_version_unique"},
    ),
    (
        "backtests",
        [("backtest_id", ASCENDING)],
        {"unique": True, "name": "backtests_backtest_id_unique"},
    ),
    ("backtests", [("model_version", ASCENDING)], {"name": "backtests_model_version"}),
    (
        "affiliate_clicks",
        [("click_id", ASCENDING)],
        {"unique": True, "name": "affiliate_clicks_click_id_unique"},
    ),
    (
        "affiliate_clicks",
        [("timestamp", ASCENDING), ("sportsbook", ASCENDING)],
        {"name": "affiliate_clicks_timestamp_sportsbook"},
    ),
    (
        "affiliate_campaigns",
        [("campaign_id", ASCENDING)],
        {"unique": True, "name": "affiliate_campaigns_campaign_id_unique"},
    ),
    (
        # Phase 8 — sportsbook partners. Starts EMPTY; URLs are
        # admin-configured, never hardcoded or invented.
        "sportsbooks",
        [("sportsbook_id", ASCENDING)],
        {"unique": True, "name": "sportsbooks_sportsbook_id_unique"},
    ),
    (
        "sportsbooks",
        [("name", ASCENDING)],
        {"name": "sportsbooks_name"},
    ),
    (
        # Phase 8 — conversions transcribed from real affiliate reports.
        "conversions",
        [("conversion_id", ASCENDING)],
        {"unique": True, "name": "conversions_conversion_id_unique"},
    ),
    (
        "conversions",
        [("sportsbook_id", ASCENDING), ("converted_at", ASCENDING)],
        {"name": "conversions_sportsbook_converted"},
    ),
    ("users", [("email", ASCENDING)], {"unique": True, "name": "users_email_unique"}),
    ("sessions", [("session_id", ASCENDING)], {"unique": True, "name": "sessions_session_id_unique"}),
    (
        "subscriptions",
        [("subscription_id", ASCENDING)],
        {"unique": True, "name": "subscriptions_subscription_id_unique"},
    ),
    ("subscriptions", [("user_id", ASCENDING)], {"name": "subscriptions_user_id"}),
    (
        # Phase 9 — webhook lookup by Stripe subscription id (non-unique:
        # a historical trail is allowed; the live row is the latest).
        "subscriptions",
        [("stripe_subscription_id", ASCENDING)],
        {"name": "subscriptions_stripe_subscription_id"},
    ),
    (
        # Phase 9 — Stripe webhook idempotency: one record per event id.
        "stripe_events",
        [("event_id", ASCENDING)],
        {"unique": True, "name": "stripe_events_event_id_unique"},
    ),
    (
        # Phase 9 — daily usage counters for plan gating.
        "usage_counters",
        [("key", ASCENDING), ("day", ASCENDING)],
        {"unique": True, "name": "usage_counters_key_day_unique"},
    ),
    (
        # Phase 9 — premium bet alerts.
        "alerts",
        [("alert_id", ASCENDING)],
        {"unique": True, "name": "alerts_alert_id_unique"},
    ),
    ("alerts", [("user_id", ASCENDING)], {"name": "alerts_user_id"}),
    ("analytics", [("timestamp", ASCENDING), ("event", ASCENDING)], {"name": "analytics_timestamp_event"}),
    (
        "scheduler_jobs",
        [("job_id", ASCENDING)],
        {"unique": True, "name": "scheduler_jobs_job_id_unique"},
    ),
    ("scheduler_jobs", [("name", ASCENDING)], {"unique": True, "name": "scheduler_jobs_name_unique"}),
    (
        # Phase 10 — scheduler run records (append-only audit trail).
        "scheduler_runs",
        [("run_id", ASCENDING)],
        {"unique": True, "name": "scheduler_runs_run_id_unique"},
    ),
    (
        "scheduler_runs",
        [("job_name", ASCENDING), ("started_at", DESCENDING)],
        {"name": "scheduler_runs_job_started"},
    ),
    (
        # Phase 10 — scheduler alerts (failure / crash / missed-run).
        "scheduler_alerts",
        [("alert_id", ASCENDING)],
        {"unique": True, "name": "scheduler_alerts_id_unique"},
    ),
    (
        "scheduler_alerts",
        [("job_name", ASCENDING), ("created_at", DESCENDING)],
        {"name": "scheduler_alerts_job_created"},
    ),
    (
        "ingestion_runs",
        [("run_id", ASCENDING)],
        {"unique": True, "name": "ingestion_runs_run_id_unique"},
    ),
    (
        "ingestion_runs",
        [("source", ASCENDING), ("finished_at", ASCENDING)],
        {"name": "ingestion_runs_source_finished"},
    ),
    (
        "conversations",
        [("session_id", ASCENDING)],
        {"unique": True, "name": "conversations_session_id_unique"},
    ),
    (
        "conversations",
        [("user_id", ASCENDING), ("updated_at", DESCENDING)],
        {"name": "conversations_user_updated"},
    ),
]


def ensure_indexes(db: Any) -> None:
    """Create all indexes on the given database.

    Accepts any object exposing the pymongo collection API
    (real pymongo database or mongomock in tests). Idempotent.
    """
    for collection, keys, kwargs in INDEX_SPECS:
        db[collection].create_index(keys, **kwargs)
