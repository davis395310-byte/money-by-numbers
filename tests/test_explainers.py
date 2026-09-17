"""Tests for the tipping-point explainer (paid-tier feature).

Honesty contract under test:
- every clause of the explainer maps to a real pick input;
- the rating gap is recovered from the locked probability via the
  champion formula, so the explainer can never contradict the pick;
- market comparison appears only with a real odds snapshot;
- gating: paid plans (or year-one-free) see explainers; free plan does not.
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.picks.explainers import (  # noqa: E402
    build_explainer,
    implied_rating_gap,
    paid_access_granted,
    possessive,
)


def _pred(**kw):
    base = {
        "prediction_id": "pred_x",
        "predicted_winner": "SEA",
        "home_team": "SEA",
        "away_team": "NE",
        "model_probability": 0.7153,
        "confidence": "HIGH",
        "model_version": "MNB-NFL-2026.01",
    }
    base.update(kw)
    return base


def test_implied_gap_inverts_champion_formula():
    # p = 1 / (1 + 10^(-(gap + 35)/400))
    for p in (0.5, 0.5899, 0.7153, 0.95):
        gap = implied_rating_gap(p)
        back = 1.0 / (1.0 + 10.0 ** (-(gap + 35.0) / 400.0))
        assert abs(back - p) < 1e-9


def test_home_pick_names_gap_and_home_field():
    out = build_explainer(_pred())
    assert "Seattle Seahawks" in out["explainer"]
    assert "71.5%" in out["explainer"]
    assert "125-point rating gap" in out["explainer"]
    assert "home field" in out["explainer"].lower()
    factors = {t["factor"] for t in out["tipping_points"]}
    assert factors == {"rating_gap", "home_field"}


def test_away_pick_describes_overcoming_home_field():
    out = build_explainer(
        _pred(
            predicted_winner="CHI",
            home_team="CAR",
            away_team="CHI",
            model_probability=0.6441,
            confidence="MEDIUM",
        )
    )
    assert "overcome" in out["explainer"]
    assert "Carolina Panthers" in out["explainer"]
    factors = {t["factor"] for t in out["tipping_points"]}
    assert "home_field_overcome" in factors


def test_coin_flip_game_says_so():
    out = build_explainer(
        _pred(
            predicted_winner="TEN",
            home_team="TEN",
            away_team="NYJ",
            model_probability=0.503,
            confidence="LOW",
        )
    )
    assert "coin flip" in out["explainer"]


def test_market_sentence_only_with_snapshot():
    plain = build_explainer(_pred())
    assert "market" not in plain["explainer"].lower()
    assert not any(t["factor"] == "market" for t in plain["tipping_points"])

    with_mkt = build_explainer(
        _pred(),
        market={"moneyline_home": -250, "moneyline_away": 210, "snapshot_at": "t"},
    )
    assert "books price" in with_mkt["explainer"]
    assert any(t["factor"] == "market" for t in with_mkt["tipping_points"])


def test_possessive():
    assert possessive("Rams") == "Rams'"
    assert possessive("Seahawks") == "Seahawks'"
    assert possessive("Heat") == "Heat's"


def test_no_fabricated_reasons():
    out = build_explainer(_pred())
    text = out["explainer"].lower()
    for banned in ("injur", "weather", "momentum", "revenge", "must-win"):
        assert banned not in text, banned


def test_gating_year_one_free_unlocks_everyone():
    granted, reason = paid_access_granted(db=None, request=None, year_one_free=True)
    assert granted and reason == "year_one_free"


def test_gating_free_plan_denied_after_year_one(monkeypatch):
    import app.picks.explainers as ex

    monkeypatch.setattr(ex, "get_plan", lambda db, key: "free", raising=False)

    # Patch the lazy import inside paid_access_granted
    import types

    fake_identity = types.SimpleNamespace(
        get_plan=lambda db, key: "free",
        resolve_requester=lambda req: "user@example.com",
    )
    monkeypatch.setitem(sys.modules, "app.billing.identity", fake_identity)
    granted, reason = paid_access_granted(
        db=object(), request=object(), year_one_free=False
    )
    assert not granted and reason == "free_plan"


def test_gating_paid_plan_allowed_after_year_one(monkeypatch):
    import types

    fake_identity = types.SimpleNamespace(
        get_plan=lambda db, key: "pro",
        resolve_requester=lambda req: "user@example.com",
    )
    monkeypatch.setitem(sys.modules, "app.billing.identity", fake_identity)
    granted, reason = paid_access_granted(
        db=object(), request=object(), year_one_free=False
    )
    assert granted and reason == "plan:pro"
