"""Tests for NFL ingestion: validation, de-duplication, pipeline, provider
normalization, and data health (product spec section 66, DATA).

All tests run offline: the pipeline is exercised with a stub provider and
mongomock, and provider normalization is tested with fixture DataFrames.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import mongomock
import pandas as pd
import pytest

from app.data_providers import NflverseProvider, normalize_team_abbr
from app.data_providers.base import DiskCache, RateLimiter, retry_with_backoff, utcnow
from app.database import ensure_indexes
from app.ingestion import (
    is_stale,
    run_ingestion,
    validate_game_record,
    validate_team_record,
    validate_team_stat_record,
)


def _utcnow():
    return datetime.now(timezone.utc)


def good_game(**overrides):
    game = {
        "game_id": "2024_01_KC_BAL",
        "season": 2024,
        "week": 1,
        "game_date": datetime(2024, 9, 5, 20, 20, tzinfo=timezone.utc),
        "home_team": "KC",
        "away_team": "BAL",
        "venue": "Arrowhead Stadium",
        "status": "final",
        "home_score": 27,
        "away_score": 20,
        "overtime": 0,
    }
    game.update(overrides)
    return game


def good_stat(**overrides):
    stat = {
        "game_id": "2024_01_KC_BAL",
        "season": 2024,
        "week": 1,
        "team": "KC",
        "points": 27,
        "yards_total": 400.0,
        "turnovers": 1,
        "epa_per_play": 0.12,
    }
    stat.update(overrides)
    return stat


class StubProvider:
    """Offline provider stub with configurable records."""

    name = "stub"

    def __init__(self, games=None, stats=None, teams=None):
        self._games = games or []
        self._stats = stats or []
        self._teams = teams if teams is not None else [
            {"abbr": "KC", "name": "Chiefs", "city": "Kansas City", "conference": "AFC", "division": "West"},
            {"abbr": "BAL", "name": "Ravens", "city": "Baltimore", "conference": "AFC", "division": "North"},
        ]

    def fetch_teams(self):
        from app.data_providers.base import ProviderResult

        return ProviderResult(source=self.name, records=list(self._teams))

    def fetch_games(self, seasons):
        from app.data_providers.base import ProviderResult

        return ProviderResult(source=self.name, records=list(self._games))

    def fetch_team_game_stats(self, seasons):
        from app.data_providers.base import ProviderResult

        return ProviderResult(source=self.name, records=list(self._stats))


@pytest.fixture()
def db():
    database = mongomock.MongoClient()["test_mnb"]
    ensure_indexes(database)
    return database


# ---------------------------------------------------------------------------
# Game validation
# ---------------------------------------------------------------------------


def test_valid_game_passes():
    assert validate_game_record(good_game()) == []


def test_missing_required_field_rejected():
    game = good_game()
    del game["away_team"]
    errors = validate_game_record(game)
    assert any("away_team" in e for e in errors)


def test_invalid_team_abbreviation_rejected():
    errors = validate_game_record(good_game(home_team="XX"))
    assert any("invalid team" in e for e in errors)


def test_same_team_both_sides_rejected():
    errors = validate_game_record(good_game(home_team="KC", away_team="KC"))
    assert any("must differ" in e for e in errors)


def test_negative_score_rejected():
    errors = validate_game_record(good_game(home_score=-3))
    assert any("negative" in e for e in errors)


def test_final_game_without_scores_rejected():
    errors = validate_game_record(good_game(status="final", home_score=None))
    assert any("missing score" in e for e in errors)


def test_scheduled_game_with_scores_rejected():
    errors = validate_game_record(good_game(status="scheduled"))
    assert any("already has score" in e for e in errors)


def test_scheduled_game_without_scores_passes():
    game = good_game(status="scheduled", home_score=None, away_score=None)
    assert validate_game_record(game) == []


def test_future_final_game_rejected():
    future = _utcnow() + timedelta(days=30)
    errors = validate_game_record(good_game(game_date=future, status="final"))
    assert any("future data" in e for e in errors)


def test_chronological_mismatch_rejected():
    # Season 2024, week 1, but dated in 2020.
    game = good_game(game_date=datetime(2020, 9, 5, tzinfo=timezone.utc))
    errors = validate_game_record(game)
    assert any("chronological mismatch" in e for e in errors)


def test_playoff_game_in_january_of_next_year_passes():
    game = good_game(
        game_id="2024_20_KC_BUF", week=20,
        game_date=datetime(2025, 1, 26, tzinfo=timezone.utc),
    )
    assert validate_game_record(game) == []


def test_regular_season_week17_in_january_passes():
    # 2016 week 17 was played 2017-01-01; regular-season weeks 17/18 may
    # fall in January of season+1.
    game = good_game(
        game_id="2016_17_BAL_CIN", season=2016, week=17,
        game_date=datetime(2017, 1, 1, 18, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert validate_game_record(game) == []


def test_wild_card_as_week18_in_january_passes():
    # Pre-2021 nflverse numbers Wild Card as week 18.
    game = good_game(
        game_id="2017_18_TEN_KC", season=2017, week=18,
        game_date=datetime(2018, 1, 6, 20, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    assert validate_game_record(game) == []


def test_playoff_game_in_wrong_year_rejected():
    # A week-20 game dated in the season year (December) is corrupt.
    game = good_game(
        game_id="2024_20_KC_BUF", week=20,
        game_date=datetime(2024, 12, 15, tzinfo=timezone.utc),
    )
    assert any("chronological mismatch" in e for e in validate_game_record(game))


def test_invalid_week_rejected():
    assert any("week" in e for e in validate_game_record(good_game(week=99)))


# ---------------------------------------------------------------------------
# Team / stat validation
# ---------------------------------------------------------------------------


def test_invalid_team_record_rejected():
    errors = validate_team_record({"abbr": "XX", "name": "X", "city": "Y", "conference": "AFC", "division": "Z"})
    assert any("invalid team abbreviation" in e for e in errors)


def test_stat_with_unknown_game_rejected():
    errors = validate_team_stat_record(good_stat(), known_game_ids={"other_game"})
    assert any("unknown game_id" in e for e in errors)


def test_stat_with_implausible_epa_rejected():
    errors = validate_team_stat_record(good_stat(epa_per_play=99.0), known_game_ids={"2024_01_KC_BAL"})
    assert any("epa_per_play" in e for e in errors)


# ---------------------------------------------------------------------------
# Pipeline: dedupe, upsert, ingestion_runs
# ---------------------------------------------------------------------------


def test_duplicate_games_in_batch_rejected(db):
    games = [good_game(), good_game()]  # identical game_id twice
    run = run_ingestion(db, StubProvider(games=games), [2024])
    assert run["records_received"]["games"] == 2
    assert run["records_accepted"]["games"] == 1
    assert run["records_rejected"]["games"] == 1
    assert db["games"].count_documents({}) == 1
    assert any("duplicate" in e for e in run["errors"])


def test_invalid_games_counted_not_silently_dropped(db):
    games = [good_game(), good_game(game_id="bad", home_team="XX")]
    run = run_ingestion(db, StubProvider(games=games), [2024])
    assert run["records_rejected"]["games"] == 1
    assert any("invalid team" in e for e in run["errors"])
    assert db["games"].count_documents({}) == 1


def test_rerun_is_idempotent(db):
    provider = StubProvider(games=[good_game()], stats=[good_stat()])
    run_ingestion(db, provider, [2024])
    run2 = run_ingestion(db, provider, [2024])
    assert db["games"].count_documents({}) == 1
    assert db["game_stats"].count_documents({}) == 1
    assert run2["records_accepted"]["games"] == 1
    assert run2["records_rejected"]["games"] == 0


def test_orphan_stat_rows_rejected(db):
    stats = [good_stat(game_id="2024_99_NOBODY")]  # game not in batch
    run = run_ingestion(db, StubProvider(games=[good_game()], stats=stats), [2024])
    assert run["records_rejected"]["game_stats"] == 1
    assert db["game_stats"].count_documents({}) == 0


def test_ingestion_run_document_records_counts(db):
    games = [good_game(), good_game(game_id="bad2", away_score=-1)]
    run = run_ingestion(db, StubProvider(games=games), [2024])
    doc = db["ingestion_runs"].find_one({"run_id": run["run_id"]})
    assert doc is not None
    assert doc["source"] == "stub"
    assert doc["records_received"]["games"] == 2
    assert doc["records_accepted"]["games"] == 1
    assert doc["records_rejected"]["games"] == 1
    assert doc["status"] in ("success", "partial")
    assert isinstance(doc["started_at"], datetime)


def test_ingestion_run_document_has_per_season_breakdown(db):
    games = [
        good_game(
            game_id="2023_01_KC_DET",
            season=2023,
            game_date=datetime(2023, 9, 7, 20, 20, tzinfo=timezone.utc),
        ),
        good_game(game_id="2024_01_KC_BAL", season=2024),
        good_game(game_id="2024_02_BAD", season=2024, home_team="XX"),
    ]
    run = run_ingestion(db, StubProvider(games=games), [2023, 2024])
    per_season = run["per_season"]
    assert per_season["2023"]["games"] == {"received": 1, "accepted": 1, "rejected": 0}
    assert per_season["2024"]["games"] == {"received": 2, "accepted": 1, "rejected": 1}
    # Sum of per-season buckets equals the aggregate counts.
    assert sum(b["games"]["accepted"] for b in per_season.values()) == run["records_accepted"]["games"]
    assert sum(b["games"]["rejected"] for b in per_season.values()) == run["records_rejected"]["games"]


def test_missing_values_stored_as_null_not_fabricated(db):
    game = good_game(game_id="2024_02_NE_MIA", home_team="NE", away_team="MIA", venue=None, overtime=None)
    run_ingestion(db, StubProvider(games=[game]), [2024])
    doc = db["games"].find_one({"game_id": "2024_02_NE_MIA"})
    assert doc["venue"] is None
    assert doc["overtime"] is None


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_is_stale_without_last_success():
    assert is_stale(None, ttl_hours=24) is True


def test_is_stale_with_old_timestamp():
    old = _utcnow() - timedelta(hours=49)
    assert is_stale(old, ttl_hours=48) is True
    assert is_stale(old, ttl_hours=72) is False


# ---------------------------------------------------------------------------
# Provider base mechanics (rate limit / retry / cache / normalization)
# ---------------------------------------------------------------------------


def test_retry_with_backoff_retries_then_succeeds():
    calls = {"n": 0}

    @retry_with_backoff(tries=3, base_delay=0.01, max_delay=0.05)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_with_backoff_raises_after_tries_exhausted():
    @retry_with_backoff(tries=2, base_delay=0.01, max_delay=0.05)
    def always_fails():
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        always_fails()


def test_disk_cache_roundtrip_and_ttl(tmp_path):
    cache = DiskCache(tmp_path, default_ttl_hours=24)
    assert cache.get("k") is None
    cache.put("k", b"payload")
    assert cache.get("k") == b"payload"
    assert cache.get("k", ttl_hours=0) is None  # expired immediately


def test_rate_limiter_enforces_interval():
    limiter = RateLimiter(min_interval_seconds=0.05)
    start = utcnow()
    limiter.wait()
    limiter.wait()
    elapsed = (utcnow() - start).total_seconds()
    assert elapsed >= 0.05


def test_normalize_team_abbr_legacy_mappings():
    assert normalize_team_abbr("LA") == "LAR"
    assert normalize_team_abbr("STL") == "LAR"
    assert normalize_team_abbr("SD") == "LAC"
    assert normalize_team_abbr("OAK") == "LV"
    assert normalize_team_abbr("KC") == "KC"
    assert normalize_team_abbr(None) is None


def test_nflverse_normalize_games_frame_without_network():
    df = pd.DataFrame(
        [
            {
                "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1,
                "gameday": "2024-09-05", "gametime": "20:20",
                "home_team": "KC", "away_team": "BAL",
                "home_score": 27.0, "away_score": 20.0,
                "stadium": "Arrowhead Stadium", "overtime": 0.0,
            },
            {
                "game_id": "2024_01_LA_SF", "season": 2024, "week": 1,
                "gameday": "2024-09-08", "gametime": None,
                "home_team": "SF", "away_team": "LA",  # legacy LA -> LAR
                "home_score": float("nan"), "away_score": float("nan"),
                "stadium": None, "overtime": float("nan"),
            },
        ]
    )
    records, errors = NflverseProvider.normalize_games_frame(df, [2024])
    assert errors == []
    assert len(records) == 2
    final, scheduled = records
    assert final["status"] == "final"
    assert final["home_score"] == 27 and final["away_score"] == 20
    # gametime is Eastern per nflverse convention: true kickoff instant.
    assert final["game_date"] == datetime(2024, 9, 5, 20, 20, tzinfo=ZoneInfo("America/New_York"))
    assert final["game_date"].astimezone(timezone.utc) == datetime(2024, 9, 6, 0, 20, tzinfo=timezone.utc)
    assert scheduled["status"] == "scheduled"
    assert scheduled["away_team"] == "LAR"  # normalized
    assert scheduled["venue"] is None  # missing stays None, not fabricated
    assert scheduled["home_score"] is None


def test_night_game_keeps_us_calendar_date_for_chronology():
    # A Dec 31 night game kicks off Jan 1 in UTC; chronology must use the
    # US (Eastern) game date so it is not wrongly rejected.
    game = good_game(
        game_id="2023_17_KC_LV",
        season=2023,
        week=17,
        game_date=datetime(2023, 12, 31, 20, 20, tzinfo=ZoneInfo("America/New_York")),
    )
    assert validate_game_record(game) == []


def test_nflverse_aggregate_team_stats_without_network():
    pbp = pd.DataFrame(
        [
            {"game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "posteam": "KC",
             "drive": 1, "play_type": "pass", "epa": 0.5, "success": 1.0, "yards_gained": 12.0,
             "passing_yards": 12.0, "rushing_yards": 0.0,
             "third_down_converted": 1.0, "third_down_failed": 0.0, "touchdown": 0.0,
             "interception": 0.0, "fumble_lost": 0.0, "yardline_100": 30.0},
            {"game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "posteam": "KC",
             "drive": 2, "play_type": "run", "epa": -0.2, "success": 0.0, "yards_gained": 2.0,
             "passing_yards": 0.0, "rushing_yards": 2.0,
             "third_down_converted": 0.0, "third_down_failed": 0.0, "touchdown": 1.0,
             "interception": 0.0, "fumble_lost": 0.0, "yardline_100": 15.0},
            {"game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "posteam": "KC",
             "drive": 2, "play_type": "run", "epa": 0.1, "success": 1.0, "yards_gained": 3.0,
             "passing_yards": 0.0, "rushing_yards": 3.0,
             "third_down_converted": 0.0, "third_down_failed": 0.0, "touchdown": 0.0,
             "interception": 0.0, "fumble_lost": 0.0, "yardline_100": 8.0},
            {"game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "posteam": "BAL",
             "drive": 3, "play_type": "pass", "epa": 0.1, "success": 1.0, "yards_gained": 8.0,
             "passing_yards": 8.0, "rushing_yards": 0.0,
             "third_down_converted": 0.0, "third_down_failed": 1.0, "touchdown": 0.0,
             "interception": 1.0, "fumble_lost": 0.0, "yardline_100": 50.0},
        ]
    )
    schedules = pd.DataFrame(
        [
            {"game_id": "2024_01_KC_BAL", "season": 2024,
             "home_team": "KC", "away_team": "BAL",
             "home_score": 27.0, "away_score": 20.0},
        ]
    )
    records, errors = NflverseProvider.aggregate_team_stats(pbp, schedules, 2024)
    assert errors == []
    by_team = {r["team"]: r for r in records}
    kc = by_team["KC"]
    assert kc["points"] == 27
    assert kc["plays"] == 3
    assert kc["yards_total"] == 17.0
    assert kc["turnovers"] == 0
    assert kc["third_down_conv"] == 1 and kc["third_down_att"] == 1
    # Red zone: two RZ plays on the SAME drive = one trip, ending in a TD.
    assert kc["red_zone_att"] == 1
    assert kc["red_zone_td"] == 1
    assert abs(kc["epa_per_play"] - (0.4 / 3)) < 1e-9
    bal = by_team["BAL"]
    assert bal["points"] == 20
    assert bal["turnovers"] == 1
    # Never reached the red zone: 0 trips is real data, not None.
    assert bal["red_zone_att"] == 0
    assert bal["red_zone_td"] == 0


def test_red_zone_trips_none_without_drive_column():
    # Without drive info, trips cannot be computed honestly: the fields
    # stay None rather than falling back to a play count.
    pbp = pd.DataFrame(
        [
            {"game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "posteam": "KC",
             "play_type": "pass", "epa": 0.5, "success": 1.0, "yards_gained": 12.0,
             "passing_yards": 12.0, "rushing_yards": 0.0,
             "third_down_converted": 1.0, "third_down_failed": 0.0, "touchdown": 0.0,
             "interception": 0.0, "fumble_lost": 0.0, "yardline_100": 10.0},
        ]
    )
    schedules = pd.DataFrame(
        [{"game_id": "2024_01_KC_BAL", "season": 2024,
          "home_team": "KC", "away_team": "BAL",
          "home_score": 27.0, "away_score": 20.0}]
    )
    records, errors = NflverseProvider.aggregate_team_stats(pbp, schedules, 2024)
    assert errors == []
    assert records[0]["red_zone_att"] is None
    assert records[0]["red_zone_td"] is None


# ---------------------------------------------------------------------------
# Data health endpoint with a seeded database
# ---------------------------------------------------------------------------


def test_data_health_reports_real_counts_and_freshness(db, monkeypatch):
    now = _utcnow()
    db["games"].insert_one({"game_id": "g1", "ingested_at": now})
    db["teams"].insert_one({"team_id": "KC", "ingested_at": now})
    db["game_stats"].insert_one({"game_id": "g1", "team_id": "KC", "ingested_at": now})
    db["ingestion_runs"].insert_one(
        {
            "run_id": "r1", "source": "stub", "status": "success",
            "started_at": now, "finished_at": now,
            "records_accepted": {"games": 1}, "records_rejected": {"games": 0},
            "errors": [],
        }
    )
    import app.routers.data_health as dh
    from types import SimpleNamespace

    # The router short-circuits when MONGODB_URI is unset (the test env
    # clears it); stub settings so the monkeypatched client is used.
    monkeypatch.setattr(
        dh, "get_settings", lambda: SimpleNamespace(MONGODB_URI="mongodb://fake")
    )

    holder = {"db": db}

    class FakeClient:
        def __getitem__(self, name):
            return holder["db"]

        def close(self):
            pass

    monkeypatch.setattr(dh, "get_mongo_client", lambda: FakeClient())
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/api/data/health").json()
    assert body["games"]["record_count"] == 1
    assert body["games"]["status"] == "ok"
    assert body["games"]["freshness"] == "fresh"
    assert body["games"]["last_run_id"] == "r1"
    # Phase 2 datasets are reported too, with per-dataset TTLs.
    assert body["teams"]["record_count"] == 1
    assert body["teams"]["freshness"] == "fresh"
    assert body["game_stats"]["record_count"] == 1
    assert body["game_stats"]["freshness"] == "fresh"
    # Future phases stay honestly empty.
    assert body["odds"]["status"] == "not_yet_ingested"
    assert body["odds"]["record_count"] == 0
    assert body["ingestion"]["last_run"]["run_id"] == "r1"
    assert body["errors"] == []


def test_data_health_flags_stale_ingestion(db, monkeypatch):
    old = _utcnow() - timedelta(days=30)
    db["games"].insert_one({"game_id": "g1", "ingested_at": old})
    db["ingestion_runs"].insert_one(
        {
            "run_id": "r-old", "source": "stub", "status": "success",
            "started_at": old, "finished_at": old,
            "records_accepted": {"games": 1}, "records_rejected": {"games": 0},
            "errors": ["example warning: row skipped"],
        }
    )
    import app.routers.data_health as dh
    from types import SimpleNamespace

    monkeypatch.setattr(
        dh, "get_settings", lambda: SimpleNamespace(MONGODB_URI="mongodb://fake")
    )

    holder = {"db": db}

    class FakeClient:
        def __getitem__(self, name):
            return holder["db"]

        def close(self):
            pass

    monkeypatch.setattr(dh, "get_mongo_client", lambda: FakeClient())
    monkeypatch.setattr(dh, "get_database_name", lambda: "test_mnb")
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/api/data/health").json()
    assert body["games"]["freshness"] == "stale"
    assert any("example warning" in e for e in body["errors"])
