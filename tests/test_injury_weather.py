"""Tests for Phase 5: injuries + weather (product spec section 66, DATA).

All tests run offline: provider normalization is tested with fixture
DataFrames/JSON, the pipeline with stub providers and mongomock, and the
API with FastAPI's TestClient. Live-source verification (real nflverse
injuries release, real Open-Meteo responses) was done by the builder on
2026-09-09 and is documented in the provider docstrings — not in tests,
which must stay hermetic.
"""

from datetime import datetime, timedelta, timezone

import mongomock
import pandas as pd
import pytest

from app.data_providers import (
    NflverseInjuryProvider,
    OpenMeteoWeatherProvider,
    lookup_stadium,
)
from app.data_providers.weather import (
    _nearest_hour_index,
    classify_roof,
    describe_weathercode,
)
from app.ingestion import (
    ingest_injuries,
    ingest_weather,
    injury_warnings,
    is_stale,
    run_context_ingestion,
    validate_injury_record,
    validate_weather_record,
)


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def injuries_frame():
    return pd.DataFrame(
        [
            {
                "season": 2024, "game_type": "REG", "team": "ARI", "week": 1,
                "gsis_id": "00-0039521", "position": "WR",
                "full_name": "Xavier Weaver", "first_name": "Xavier", "last_name": "Weaver",
                "report_primary_injury": "Oblique", "report_secondary_injury": None,
                "report_status": "Out",
                "practice_primary_injury": "Oblique", "practice_secondary_injury": None,
                "practice_status": "Did Not Participate In Practice",
                "date_modified": "2024-09-06 19:05:30+00:00",
            },
            {
                "season": 2024, "game_type": "REG", "team": "KC", "week": 1,
                "gsis_id": "00-0030001", "position": "QB",
                "full_name": "Test Player", "first_name": "Test", "last_name": "Player",
                "report_primary_injury": "Ankle", "report_secondary_injury": None,
                "report_status": "Questionable",
                "practice_primary_injury": "Ankle", "practice_secondary_injury": None,
                "practice_status": "Limited Participation in Practice",
                "date_modified": "2024-09-06 19:05:30+00:00",
            },
            {
                # Bad row: unrecognized team — must be reported, not crash.
                "season": 2024, "game_type": "REG", "team": "XX", "week": 1,
                "gsis_id": "00-0000000", "position": "RB",
                "full_name": "Bad Team Guy", "first_name": "Bad", "last_name": "Guy",
                "report_primary_injury": None, "report_secondary_injury": None,
                "report_status": "Out",
                "practice_primary_injury": None, "practice_secondary_injury": None,
                "practice_status": None,
                "date_modified": None,
            },
        ]
    )


def good_injury(**overrides):
    record = {
        "season": 2024, "week": 1, "game_type": "REG", "team": "KC",
        "gsis_id": "00-0030001", "position": "QB", "full_name": "Test Player",
        "report_status": "Questionable", "report_primary_injury": "Ankle",
        "practice_status": "Limited Participation in Practice",
        "date_modified": "2024-09-06T19:05:30+00:00",
    }
    record.update(overrides)
    return record


def good_weather(**overrides):
    record = {
        "game_id": "2024_01_KC_BAL",
        "venue": "Arrowhead Stadium",
        "venue_matched": "Arrowhead Stadium",
        "latitude": 39.0489, "longitude": -94.4839,
        "kickoff": "2024-09-05T20:20:00+00:00",
        "temp_f": 72.5, "wind_mph": 8.0, "wind_gust_mph": 12.0,
        "precip_prob": 0.1, "precip_in": 0.0,
        "weathercode": 2, "conditions": "Partly cloudy",
        "dome": False, "source": "open-meteo",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Injury provider normalization
# ---------------------------------------------------------------------------


def test_normalize_injuries_frame_parses_rows():
    records, errors = NflverseInjuryProvider.normalize_injuries_frame(injuries_frame(), 2024)
    assert len(records) == 3  # raw values kept; validate_injury_record rejects the bad team downstream
    assert records[2]["team"] == "XX"
    assert validate_injury_record(records[2])  # bad team rejected at validation
    first = records[0]
    assert first["team"] == "ARI"
    assert first["full_name"] == "Xavier Weaver"
    assert first["report_status"] == "Out"
    assert first["practice_status"] == "Did Not Participate In Practice"
    assert first["date_modified"].startswith("2024-09-06")
    assert first["source"] == "nflverse-injuries"
    first = records[0]
    assert first["team"] == "ARI"
    assert first["full_name"] == "Xavier Weaver"
    assert first["report_status"] == "Out"
    assert first["practice_status"] == "Did Not Participate In Practice"
    assert first["date_modified"].startswith("2024-09-06")
    assert first["source"] == "nflverse-injuries"


def test_injury_provider_needs_no_auth():
    provider = NflverseInjuryProvider()
    assert provider.auth_configured() is True
    assert provider.requires_auth is False


def test_injury_coverage_is_honest():
    coverage = NflverseInjuryProvider().coverage()
    assert coverage["live_wire"] is False
    assert "2009" in str(coverage["first_season"]) or coverage["first_season"] == 2009


# ---------------------------------------------------------------------------
# Injury validation
# ---------------------------------------------------------------------------


def test_validate_injury_record_accepts_good():
    assert validate_injury_record(good_injury()) == []


def test_validate_injury_record_rejects_bad_team():
    errors = validate_injury_record(good_injury(team="XX"))
    assert any("invalid team" in e for e in errors)


def test_validate_injury_record_rejects_missing_name():
    errors = validate_injury_record(good_injury(full_name=None))
    assert any("missing required field: full_name" in e for e in errors)


def test_validate_injury_record_rejects_future_modified():
    future = (_utcnow() + timedelta(days=30)).isoformat()
    errors = validate_injury_record(good_injury(date_modified=future))
    assert any("future" in e for e in errors)


def test_validate_injury_record_rejects_bad_week():
    errors = validate_injury_record(good_injury(week=99))
    assert any("invalid week" in e for e in errors)


def test_unknown_report_status_accepted_with_warning():
    record = good_injury(report_status="Note")
    assert validate_injury_record(record) == []  # accepted, not rejected
    warnings = injury_warnings(record)
    assert any("Note" in w for w in warnings)


def test_unknown_practice_status_accepted_with_warning():
    record = good_injury(practice_status="Walkthrough Only")
    assert validate_injury_record(record) == []
    assert any("Walkthrough Only" in w for w in injury_warnings(record))


# ---------------------------------------------------------------------------
# Weather helpers
# ---------------------------------------------------------------------------


def test_describe_weathercode():
    assert describe_weathercode(0) == "Clear sky"
    assert describe_weathercode(95) == "Thunderstorm"
    assert describe_weathercode(999) is None
    assert describe_weathercode(None) is None


def test_classify_roof():
    assert classify_roof("dome") is True
    assert classify_roof("closed") is True
    assert classify_roof("outdoors") is False
    assert classify_roof("open") is False
    assert classify_roof("retractable") is None  # unknown at fetch time — never guessed
    assert classify_roof(None) is None
    assert classify_roof("weird") is None


def test_nearest_hour_index():
    times = ["2024-09-05T18:00", "2024-09-05T19:00", "2024-09-05T20:00"]
    kickoff = datetime(2024, 9, 5, 19, 20, tzinfo=timezone.utc)
    assert _nearest_hour_index(times, kickoff) == 1
    assert _nearest_hour_index([], kickoff) is None


def test_stadium_lookup():
    assert lookup_stadium("Lumen Field") == (47.5952, -122.3316)
    assert lookup_stadium("lumen field") == (47.5952, -122.3316)
    assert lookup_stadium("Levi's Stadium") == (37.4030, -121.9700)
    assert lookup_stadium("Nonexistent Dome") is None
    assert lookup_stadium(None) is None
    assert lookup_stadium("") is None


def test_weather_provider_needs_no_auth():
    provider = OpenMeteoWeatherProvider()
    assert provider.auth_configured() is True


def test_weather_provider_honest_failure(monkeypatch):
    provider = OpenMeteoWeatherProvider()

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(provider, "_get_json", boom)
    result = provider.fetch_game_weather(
        [{"game_id": "g1", "venue": "Lumen Field",
          "kickoff": _utcnow() + timedelta(hours=5)}]
    )
    assert result.records == []
    assert any("g1" in e for e in result.errors)  # failure recorded, not raised


def test_weather_skips_beyond_forecast_range():
    provider = OpenMeteoWeatherProvider()
    far = _utcnow() + timedelta(days=60)
    result = provider.fetch_game_weather(
        [{"game_id": "g2", "venue": "Lumen Field", "kickoff": far}]
    )
    assert result.records == []
    assert any("forecast range" in e for e in result.errors)


def test_weather_skips_missing_venue():
    provider = OpenMeteoWeatherProvider()
    result = provider.fetch_game_weather(
        [{"game_id": "g3", "venue": "", "kickoff": _utcnow()}]
    )
    assert result.records == []
    assert any("no venue" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Weather validation
# ---------------------------------------------------------------------------


def test_validate_weather_record_accepts_good():
    assert validate_weather_record(good_weather()) == []


def test_validate_weather_record_allows_null_measurements():
    record = good_weather(temp_f=None, wind_mph=None, precip_prob=None)
    assert validate_weather_record(record) == []  # nulls stay null, never invented


def test_validate_weather_record_rejects_bad_coords():
    errors = validate_weather_record(good_weather(latitude=999))
    assert any("latitude" in e for e in errors)


def test_validate_weather_record_rejects_implausible_temp():
    errors = validate_weather_record(good_weather(temp_f=200))
    assert any("temp_f" in e for e in errors)


def test_validate_weather_record_rejects_bad_precip_prob():
    errors = validate_weather_record(good_weather(precip_prob=1.5))
    assert any("precip_prob" in e for e in errors)


def test_validate_weather_record_rejects_missing_game_id():
    errors = validate_weather_record(good_weather(game_id=None))
    assert any("game_id" in e for e in errors)


# ---------------------------------------------------------------------------
# Pipeline: injuries + weather with stub providers
# ---------------------------------------------------------------------------


class StubInjuryProvider:
    name = "stub-injuries"

    def __init__(self, records, errors=()):
        self._records = records
        self._errors = list(errors)

    def fetch_injuries(self, seasons):
        from app.data_providers.base import ProviderResult, utcnow
        return ProviderResult(source=self.name, records=self._records,
                              fetched_at=utcnow(), errors=self._errors)


class StubWeatherProvider:
    name = "stub-weather"

    def __init__(self, records, errors=()):
        self._records = records
        self._errors = list(errors)

    def fetch_game_weather(self, games):
        from app.data_providers.base import ProviderResult, utcnow
        return ProviderResult(source=self.name, records=self._records,
                              fetched_at=utcnow(), errors=self._errors)


def test_ingest_injuries_pipeline_counts():
    db = mongomock.MongoClient()["test"]
    records = [good_injury(),
               good_injury(gsis_id="00-0030002", full_name="Second Player", team="BAL"),
               good_injury(gsis_id="bad", full_name="Bad", team="XX")]  # rejected
    summary = ingest_injuries(db, StubInjuryProvider(records), [2024], _utcnow())
    assert summary["received"] == 3
    assert summary["accepted"] == 2
    assert summary["rejected"] == 1
    assert db["injuries"].count_documents({}) == 2
    assert summary["per_season"][2024]["accepted"] == 2


def test_ingest_injuries_idempotent():
    db = mongomock.MongoClient()["test"]
    provider = StubInjuryProvider([good_injury()])
    ingest_injuries(db, provider, [2024], _utcnow())
    ingest_injuries(db, provider, [2024], _utcnow())
    assert db["injuries"].count_documents({}) == 1  # upsert, no duplicates


def test_ingest_weather_pipeline_counts():
    db = mongomock.MongoClient()["test"]
    records = [good_weather(),
               good_weather(game_id="bad", temp_f=500)]  # rejected: implausible
    summary = ingest_weather(db, StubWeatherProvider(records),
                             [{"game_id": "2024_01_KC_BAL"}], _utcnow())
    assert summary["received"] == 2
    assert summary["accepted"] == 1
    assert summary["rejected"] == 1
    assert db["weather"].count_documents({}) == 1


def test_run_context_ingestion_writes_run_doc():
    db = mongomock.MongoClient()["test"]
    run_doc = run_context_ingestion(
        db, StubInjuryProvider([good_injury()]),
        StubWeatherProvider([good_weather()]),
        [2024], [{"game_id": "2024_01_KC_BAL"}],
    )
    assert run_doc["records_accepted"] == {"injuries": 1, "weather": 1}
    assert run_doc["status"] in ("success", "partial")
    assert db["ingestion_runs"].count_documents({}) == 1


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_is_stale_flags_old_ingestion():
    old = _utcnow() - timedelta(hours=200)
    assert is_stale(old, ttl_hours=7 * 24) is True
    assert is_stale(_utcnow(), ttl_hours=7 * 24) is False
    assert is_stale(None, ttl_hours=24) is True


# ---------------------------------------------------------------------------
# Game context API
# ---------------------------------------------------------------------------


def _seed_context_db():
    from fastapi.testclient import TestClient
    from app.main import app

    db = mongomock.MongoClient()["test"]
    db["games"].insert_one({
        "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1,
        "game_date": datetime(2024, 9, 5, 20, 20),
        "home_team": "KC", "away_team": "BAL",
        "venue": "Arrowhead Stadium", "roof": "outdoors", "status": "final",
        "ingested_at": _utcnow(),
    })
    db["injuries"].insert_one({
        "season": 2024, "week": 1, "team": "KC", "full_name": "Test Player",
        "position": "QB", "report_status": "Questionable",
        "report_primary_injury": "Ankle", "practice_status": "Limited Participation in Practice",
        "ingested_at": _utcnow(),
    })
    db["weather"].insert_one({
        "game_id": "2024_01_KC_BAL", "venue": "Arrowhead Stadium",
        "kickoff": "2024-09-05T20:20:00+00:00",
        "temp_f": 72.5, "wind_mph": 8.0, "conditions": "Partly cloudy",
        "dome": False, "source": "open-meteo", "ingested_at": _utcnow(),
    })
    return db, TestClient(app)


def test_game_context_no_db_is_honest():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)  # conftest strips MONGODB_URI
    response = client.get("/api/games/2024_01_KC_BAL/context")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_context"
    assert "database not configured" in body["reason"]


def test_games_list_no_db_is_honest():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/games").json()
    assert body["status"] == "no_games"
    assert body["games"] == []


def test_game_context_joins_injuries_and_weather(monkeypatch):
    import app.routers.game_context as gc

    db, client = _seed_context_db()
    monkeypatch.setattr(gc, "_db", lambda: db)
    body = client.get("/api/games/2024_01_KC_BAL/context").json()
    assert body["status"] == "ok"
    assert body["game"]["venue"] == "Arrowhead Stadium"
    assert body["injuries"]["status"] == "ok"
    assert body["injuries"]["summary"]["Questionable"] == 1
    assert body["injuries"]["records"][0]["full_name"] == "Test Player"
    assert body["weather"]["status"] == "ok"
    assert body["weather"]["record"]["temp_f"] == 72.5
    assert body["weather"]["record"]["dome"] is False


def test_game_context_missing_sections_are_unavailable(monkeypatch):
    import app.routers.game_context as gc

    db, client = _seed_context_db()
    db["weather"].delete_many({})
    monkeypatch.setattr(gc, "_db", lambda: db)
    body = client.get("/api/games/2024_01_KC_BAL/context").json()
    assert body["weather"]["status"] == "unavailable"
    assert "reason" in body["weather"]
    # injuries still present
    assert body["injuries"]["status"] == "ok"


def test_game_context_unknown_game(monkeypatch):
    import app.routers.game_context as gc

    db, client = _seed_context_db()
    monkeypatch.setattr(gc, "_db", lambda: db)
    body = client.get("/api/games/NOPE/context").json()
    assert body["status"] == "no_context"
    assert "unknown game_id" in body["reason"]


# ---------------------------------------------------------------------------
# Data health: dataset-specific run freshness
# ---------------------------------------------------------------------------


def test_data_health_run_is_dataset_specific(monkeypatch):
    import app.routers.data_health as dh
    from types import SimpleNamespace

    now = _utcnow()
    db = mongomock.MongoClient()["test"]
    db["injuries"].insert_one(
        {"season": 2024, "week": 1, "team": "KC", "full_name": "Test Player",
         "ingested_at": now}
    )
    db["games"].insert_one({"game_id": "g1", "ingested_at": now})
    # A context run that only ingested injuries must not claim games.
    db["ingestion_runs"].insert_one(
        {
            "run_id": "r-ctx", "source": "stub-injuries+stub-weather", "status": "success",
            "started_at": now, "finished_at": now,
            "records_accepted": {"injuries": 5, "weather": 0},
            "records_rejected": {"injuries": 0, "weather": 0},
            "errors": [],
        }
    )
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
    assert body["injuries"]["last_run_id"] == "r-ctx"
    assert body["injuries"]["freshness"] == "fresh"
    assert body["games"]["last_run_id"] is None  # unrelated run claims nothing
    # games freshness still comes from its own document timestamps
    assert body["games"]["freshness"] == "fresh"


# ---------------------------------------------------------------------------
# Pydantic models match the real source data
# ---------------------------------------------------------------------------


def test_injury_model_keeps_raw_status_verbatim():
    from app.models.core import Injury

    injury = Injury(
        season=2024, week=1, team="KC", full_name="Test Player",
        report_status="Note",  # real nflverse value; must not be rejected or rewritten
        practice_status="Limited Participation in Practice",
    )
    assert injury.report_status == "Note"


def test_injury_model_rejects_bad_team():
    from app.models.core import Injury

    import pytest

    with pytest.raises(Exception):
        Injury(season=2024, week=1, team="XX", full_name="Test Player")


def test_weather_model_allows_all_null_measurements():
    from app.models.core import Weather

    weather = Weather(game_id="g1", dome=True)
    assert weather.temp_f is None
    assert weather.wind_mph is None
    assert weather.precip_prob is None
