"""Tests for the weekly picks lock job (pipelines/jobs/weekly_picks.py).

Covers the integrity contract Richard's public board depends on:
- partial weeks are never locked (no backfilling),
- post-kickoff games are refused by the ledger,
- duplicates are refused,
- dry-run writes nothing,
- explainers are generated for locked picks,
- the champion version comes from the models registry.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import mongomock
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipelines.jobs.weekly_picks import WeeklyPicksJob, _target_week
from pipelines.schedule import JOB_SCHEDULES, get_schedule
from pipelines.scheduler import JobStatus, SkipJob

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)  # a Tuesday, in season


def _game(game_id, season, week, home, away, kickoff, hs=None, aws=None):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff,
        "home_score": hs,
        "away_score": aws,
        "status": "final" if hs is not None else "scheduled",
        "home_rest": 7,
        "away_rest": 7,
        "div_game": 0,
        "roof": "outdoors",
        "location": "Home",
    }


def _training_games():
    """A thin but sufficient 2016-2025 history for the margin-slope fit."""
    rows = []
    base = datetime(2020, 9, 13, 17, 0, tzinfo=timezone.utc)
    for i in range(12):
        season = 2016 + (i % 10)
        ko = base + timedelta(weeks=i * 40)
        hs = 24 + (i % 5)
        aws = 20 + (i % 3)
        rows.append(
            _game(f"{season}_01_KC_BUF", season, 1, "KC", "BUF", ko, hs, aws)
        )
    return rows


def _frame(rows):
    return pd.DataFrame(rows)


@pytest.fixture()
def db():
    return mongomock.MongoClient()["money_by_numbers"]


def _run_job(db, rows, now=NOW, dry_run=False):
    import ml.data_loader as dl

    real = dl.load_games
    dl.load_games = lambda *a, **k: _frame(rows)
    try:
        job = WeeklyPicksJob()
        return job.run({"db": db, "now": now, "dry_run": dry_run})
    finally:
        dl.load_games = real


# ---------------------------------------------------------------------------
# Target-week selection
# ---------------------------------------------------------------------------

def test_target_week_picks_earliest_all_future_week():
    rows = _training_games() + [
        _game("2026_03_GB_MIN", 2026, 3, "GB", "MIN",
              NOW + timedelta(days=2), None, None),
        _game("2026_04_GB_MIN", 2026, 4, "GB", "MIN",
              NOW + timedelta(days=9), None, None),
    ]
    assert _target_week(_frame(rows), 2026, NOW) == 3


def test_target_week_skips_partial_week():
    # Week 3 has one final already: it must never be locked.
    rows = _training_games() + [
        _game("2026_03_A_B", 2026, 3, "KC", "BUF",
              NOW - timedelta(days=3), 21, 17),
        _game("2026_03_GB_MIN", 2026, 3, "GB", "MIN",
              NOW + timedelta(hours=5), None, None),
        _game("2026_04_GB_MIN", 2026, 4, "GB", "MIN",
              NOW + timedelta(days=9), None, None),
    ]
    assert _target_week(_frame(rows), 2026, NOW) == 4


def test_target_week_none_lockable_raises():
    rows = _training_games() + [
        _game("2026_03_A_B", 2026, 3, "KC", "BUF",
              NOW - timedelta(days=3), 21, 17),
    ]
    with pytest.raises(SkipJob):
        _target_week(_frame(rows), 2026, NOW)


# ---------------------------------------------------------------------------
# Lock behavior
# ---------------------------------------------------------------------------

def _week3_rows():
    return _training_games() + [
        _game("2026_03_PHI_DAL", 2026, 3, "PHI", "DAL",
              NOW + timedelta(days=2), None, None),
        _game("2026_03_SF_SEA", 2026, 3, "SF", "SEA",
              NOW + timedelta(days=3), None, None),
    ]


def test_dry_run_writes_nothing(db):
    record = _run_job(db, _week3_rows(), dry_run=True)
    assert record.status == JobStatus.SUCCEEDED
    assert record.detail["dry_run"] is True
    assert record.detail["locked"] == 2
    assert len(record.detail["board"]) == 2
    assert db["predictions"].count_documents({}) == 0
    assert db["pick_explainers"].count_documents({}) == 0


def test_lock_writes_picks_and_explainers(db):
    record = _run_job(db, _week3_rows())
    assert record.status == JobStatus.SUCCEEDED
    assert record.detail["locked"] == 2
    assert record.detail["week"] == 3
    assert db["predictions"].count_documents({}) == 2
    assert db["pick_explainers"].count_documents({}) == 2
    pick = db["predictions"].find_one({"game_id": "2026_03_PHI_DAL"})
    assert pick["model_version"] == "MNB-NFL-2026.01"  # registry fallback recorded
    assert record.detail["champion_source"] == "fallback_constant"
    assert pick["predicted_winner"] in ("PHI", "DAL")
    assert 0.5 <= pick["model_probability"] <= 1.0
    assert isinstance(pick["predicted_home_score"], int)
    explainer = db["pick_explainers"].find_one(
        {"prediction_id": pick["prediction_id"]}
    )
    assert explainer is not None
    assert explainer["explainer"]  # non-empty tipping-point text


def test_champion_version_comes_from_registry(db):
    db["models"].insert_one({"version": "MNB-NFL-2026.02", "champion": True})
    record = _run_job(db, _week3_rows())
    assert record.status == JobStatus.SUCCEEDED
    assert record.detail["champion_source"] == "models_registry"
    pick = db["predictions"].find_one({})
    assert pick["model_version"] == "MNB-NFL-2026.02"


def test_duplicate_pick_refused_not_overwritten(db):
    _run_job(db, _week3_rows())
    assert db["predictions"].count_documents({}) == 2
    record = _run_job(db, _week3_rows())
    # Second run: every game skipped as duplicate, nothing overwritten.
    assert db["predictions"].count_documents({}) == 2
    assert record.detail["locked"] == 0
    assert len(record.detail["skipped"]) == 2
    assert all("duplicate" in s["reason"] for s in record.detail["skipped"])


def test_post_kickoff_game_never_locked(db):
    rows = _training_games() + [
        _game("2026_03_DEN_KC", 2026, 3, "DEN", "KC",
              NOW - timedelta(hours=1), None, None),  # kickoff passed, no score yet
        _game("2026_03_GB_MIN", 2026, 3, "GB", "MIN",
              NOW + timedelta(days=2), None, None),
    ]
    # _target_week skips week 3 (partial) -> week 4 has no games -> SkipJob.
    record = _run_job(db, rows)
    assert record.status == JobStatus.SKIPPED
    assert db["predictions"].count_documents({}) == 0


def test_no_lockable_week_skips_honestly(db):
    rows = _training_games()
    record = _run_job(db, rows)
    assert record.status == JobStatus.SKIPPED


# ---------------------------------------------------------------------------
# Roster registration
# ---------------------------------------------------------------------------

def test_weekly_picks_in_roster():
    entry = get_schedule("weekly_picks")
    assert entry["job_path"] == "pipelines.jobs.weekly_picks:WeeklyPicksJob"
    assert entry["cadence"]["kind"] == "weekly_days"
    assert entry["cadence"]["days"] == [1]  # Tuesdays
    assert entry["in_season_only"] is True
    assert any(e["name"] == "weekly_picks" for e in JOB_SCHEDULES)
