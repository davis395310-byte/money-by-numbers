"""Tests for document models and MongoDB indexes (via mongomock)."""

from datetime import datetime, timezone

import mongomock
import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.database import ensure_indexes
from app.models.core import Game
from app.models.predictions import Prediction


def _game(**overrides):
    base = dict(
        game_id="2026_01_KC_BUF",
        season=2026,
        week=1,
        home_team="KC",
        away_team="BUF",
        game_date=datetime(2026, 9, 10, 20, 15, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Game(**base)


def test_game_rejects_invalid_team_abbreviation():
    with pytest.raises(ValidationError):
        _game(home_team="XYZ")


def test_game_rejects_negative_score():
    with pytest.raises(ValidationError):
        _game(home_score=-3)
    with pytest.raises(ValidationError):
        _game(away_score=-1)


def test_game_accepts_valid_game():
    game = _game(home_score=27, away_score=24, status="final")
    assert game.home_team == "KC"
    assert game.home_score == 27


def test_ensure_indexes_creates_unique_game_index_and_rejects_duplicates():
    db = mongomock.MongoClient().testdb
    ensure_indexes(db)  # must run cleanly against mongomock

    index_info = db.games.index_information()
    unique_indexes = [info for info in index_info.values() if info.get("unique")]
    # The unique compound index on (season, week, home_team, away_team).
    assert any(
        [key for key, _ in info["key"]] == ["season", "week", "home_team", "away_team"]
        for info in unique_indexes
    ), f"unique game index missing: {index_info}"

    game_doc = _game().model_dump(mode="json")
    db.games.insert_one(game_doc)
    with pytest.raises(DuplicateKeyError):
        db.games.insert_one(game_doc)


def _prediction(probability):
    return Prediction(
        prediction_id="pred_1",
        game_id="2026_01_KC_BUF",
        created_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        model_version="MNB-NFL-2026.02",
        training_cutoff="2025 season (pre-kickoff Week 1 2026)",
        season=2026,
        week=1,
        home_team="KC",
        away_team="BUF",
        model_probability=probability,
        predicted_winner="KC",
        confidence="medium",
    )


def test_prediction_rejects_probability_outside_unit_interval():
    with pytest.raises(ValidationError):
        _prediction(1.5)
    with pytest.raises(ValidationError):
        _prediction(-0.1)


def test_prediction_accepts_boundary_probabilities():
    assert _prediction(0.0).model_probability == 0.0
    assert _prediction(1.0).model_probability == 1.0
