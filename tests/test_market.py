"""Phase 4 market-correctness tests for MONEY BY NUMBERS.

Covers the edge engine (``backend/app/edges/engine.py``), the odds provider
(``backend/app/data_providers/odds.py``), and the odds/edges HTTP endpoints —
per the Phase 4 product spec: no-vig math, edge threshold, EV, confidence
tiers, no-key degradation, credit-budget guardrail.

HONESTY RULE: all fixtures here are SYNTHETIC (fake game ids, textbook
prices like -110) and are never presented as real market data. They exist
only to verify the math.

Imports of the parallel-agent modules are guarded: if a module is missing
the affected tests FAIL with a clear message naming the missing module
instead of erroring at collection time.
"""

from datetime import timezone
from fractions import Fraction

import mongomock
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Guarded imports (parallel-agent modules)
# ---------------------------------------------------------------------------

try:
    from app.data_providers.base import utcnow
    from app.data_providers.odds import TheOddsApiProvider, check_budget

    _ODDS_IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover - only when module is missing
    _ODDS_IMPORT_ERROR = str(exc)
    TheOddsApiProvider = None  # type: ignore[assignment,misc]
    check_budget = None  # type: ignore[assignment,misc]
    utcnow = None  # type: ignore[assignment,misc]

try:
    import app.edges.engine as _engine_module
    from app.edges import (
        DEFAULT_EDGE_THRESHOLD,
        american_to_decimal,
        american_to_implied,
        build_edge_row,
        compute_game_edges,
        confidence_tier,
        edge_vs_market,
        expected_value,
        no_vig_probabilities,
    )

    _ENGINE_IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover - only when module is missing
    _ENGINE_IMPORT_ERROR = str(exc)
    _engine_module = None  # type: ignore[assignment]
    DEFAULT_EDGE_THRESHOLD = None  # type: ignore[assignment]
    american_to_decimal = american_to_implied = None  # type: ignore[assignment]
    build_edge_row = compute_game_edges = None  # type: ignore[assignment]
    confidence_tier = edge_vs_market = None  # type: ignore[assignment]
    expected_value = no_vig_probabilities = None  # type: ignore[assignment]


def _require_engine() -> None:
    if _ENGINE_IMPORT_ERROR is not None:
        pytest.fail(
            "MISSING MODULE: backend/app/edges/engine.py was not found "
            f"(import error: {_ENGINE_IMPORT_ERROR}). The edge-engine agent "
            "has not delivered Phase 4 yet; this test will run once the "
            "module exists."
        )


def _require_odds_provider() -> None:
    if _ODDS_IMPORT_ERROR is not None:
        pytest.fail(
            "MISSING MODULE: backend/app/data_providers/odds.py was not found "
            f"(import error: {_ODDS_IMPORT_ERROR}). The odds-provider agent "
            "has not delivered Phase 4 yet; this test will run once the "
            "module exists."
        )


def _require_app():
    try:
        from app.main import app
    except ImportError as exc:
        pytest.fail(
            "MISSING MODULE: could not import backend/app/main.py "
            f"(import error: {exc})."
        )
    return app


# ---------------------------------------------------------------------------
# No-vig math
# ---------------------------------------------------------------------------


class TestNoVigMath:
    def test_american_minus110_implied_probability(self):
        _require_engine()
        assert american_to_implied(-110) == pytest.approx(110 / 210)
        # Spec check: -110 -> implied 0.5238 each (4dp).
        assert round(american_to_implied(-110), 4) == 0.5238

    def test_no_vig_minus110_pair_is_fifty_fifty(self):
        _require_engine()
        home, away = no_vig_probabilities(-110, -110)
        assert home == pytest.approx(0.50)
        assert away == pytest.approx(0.50)
        assert home + away == pytest.approx(1.0)

    def test_no_vig_minus150_plus130_computed_exactly(self):
        """-150/+130 -> no-vig home computed exactly from the formula.

        imp_home = 150/250 = 3/5; imp_away = 100/230 = 10/23;
        total = 119/115; no-vig home = (3/5) / (119/115) = 69/119 ~= 0.5798.
        NOTE: the task spec text says "~0.5882", which is a rough estimate;
        the exact no-vig normalization gives 69/119 ~= 0.5798.
        """
        _require_engine()
        home, away = no_vig_probabilities(-150, 130)
        exact_home = Fraction(69, 119)
        assert home == pytest.approx(float(exact_home), rel=1e-12)
        assert home == pytest.approx(0.5798, abs=1e-3)
        assert home + away == pytest.approx(1.0)

    def test_no_vig_returns_none_when_a_price_is_missing(self):
        _require_engine()
        assert no_vig_probabilities(-110, None) == (None, None)
        assert no_vig_probabilities(None, 130) == (None, None)


# ---------------------------------------------------------------------------
# Edge threshold
# ---------------------------------------------------------------------------


class TestEdgeThreshold:
    def test_default_threshold_is_three_percent(self):
        _require_engine()
        assert DEFAULT_EDGE_THRESHOLD == 0.03

    def test_edge_vs_market_formula(self):
        _require_engine()
        assert edge_vs_market(0.60, 0.55) == pytest.approx(0.05)

    def test_edge_above_threshold_is_flagged(self):
        """model 0.60 vs no-vig 0.55 -> edge 0.05 >= 0.03 -> flagged."""
        _require_engine()
        row = build_edge_row(
            game_id="SYNTH_00_FAKA_FAKEB",  # synthetic fixture, not a real game
            market="moneyline",
            side="home",
            line=None,
            price=-110,  # synthetic textbook price, not a real line
            model_probability=0.60,
            novig_implied=0.55,
            model_version="SYNTH-TEST",
        )
        assert row is not None
        assert row["edge"] == pytest.approx(0.05)
        assert row["edge"] >= 0.03

    def test_edge_below_threshold_is_not_flagged(self):
        """model 0.56 vs no-vig 0.55 -> edge 0.01 < 0.03 -> no row."""
        _require_engine()
        row = build_edge_row(
            game_id="SYNTH_00_FAKA_FAKEB",
            market="moneyline",
            side="home",
            line=None,
            price=-110,
            model_probability=0.56,
            novig_implied=0.55,
            model_version="SYNTH-TEST",
        )
        assert row is None

    def test_edge_exactly_at_threshold_is_flagged(self):
        """Threshold is inclusive: edge >= 0.03 is flagged.

        Uses 0.53 vs 0.50 (edge 0.030000000000000027 in float) because
        0.58 - 0.55 == 0.029999999999999916 in float and is genuinely
        below the threshold — the boundary is float-exact, not decimal.
        """
        _require_engine()
        row = build_edge_row(
            game_id="SYNTH_00_FAKA_FAKEB",
            market="moneyline",
            side="home",
            line=None,
            price=-110,
            model_probability=0.53,
            novig_implied=0.50,
            model_version="SYNTH-TEST",
        )
        assert row is not None
        assert row["edge"] == pytest.approx(0.03)

    def test_edge_none_when_inputs_missing(self):
        _require_engine()
        assert edge_vs_market(None, 0.55) is None
        assert edge_vs_market(0.60, None) is None


# ---------------------------------------------------------------------------
# Expected value
# ---------------------------------------------------------------------------


class TestExpectedValue:
    def test_american_to_decimal_plus100(self):
        _require_engine()
        assert american_to_decimal(100) == pytest.approx(2.0)

    def test_ev_model_055_at_plus100(self):
        """model 0.55 at +100 (decimal 2.0) -> EV = 0.55*2.0 - 1 = 0.10."""
        _require_engine()
        assert expected_value(0.55, 100) == pytest.approx(0.10)

    def test_ev_model_055_at_minus110(self):
        """Sanity: 0.55 at -110 -> EV = 0.55*(1+100/110) - 1 ~= 0.05."""
        _require_engine()
        assert expected_value(0.55, -110) == pytest.approx(0.05)

    def test_ev_none_when_price_missing(self):
        _require_engine()
        assert expected_value(0.55, None) is None


# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------


class TestConfidenceTiers:
    @pytest.mark.parametrize(
        "model_prob,expected",
        [
            (0.70, "HIGH"),
            (0.60, "MEDIUM"),
            (0.55, "LOW"),
            # Boundaries are inclusive on the upper tier.
            (0.68, "HIGH"),
            (0.58, "MEDIUM"),
            # Just below the boundaries drops a tier.
            (0.6799, "MEDIUM"),
            (0.5799, "LOW"),
        ],
    )
    def test_confidence_tier(self, model_prob, expected):
        _require_engine()
        assert confidence_tier(model_prob) == expected


# ---------------------------------------------------------------------------
# Edge-row construction on synthetic fixtures (never real market data)
# ---------------------------------------------------------------------------


class TestEdgeRows:
    def test_build_edge_row_labels_model_version_and_confidence(self):
        _require_engine()
        row = build_edge_row(
            game_id="SYNTH_00_FAKA_FAKEB",
            market="moneyline",
            side="home",
            line=None,
            price=-110,
            model_probability=0.70,
            novig_implied=0.50,
            model_version="SYNTH-TEST",
        )
        assert row is not None
        assert row["model_version"] == "SYNTH-TEST"
        assert row["confidence"] == "HIGH"
        assert row["edge"] == pytest.approx(0.20)
        assert row["ev"] == pytest.approx(0.70 * (1 + 100 / 110) - 1)

    def test_compute_game_edges_flags_only_the_value_side(self):
        """Synthetic: model 0.60 on home at -110/-110 -> one row, side home."""
        _require_engine()
        snapshot = {
            "moneyline_home": -110,  # synthetic prices, not real lines
            "moneyline_away": -110,
        }
        prediction = {
            "game_id": "SYNTH_00_FAKA_FAKEB",
            "model_version": "SYNTH-TEST",
            "model_probability": 0.60,
            "predicted_winner": "FAKA",
            "home_team": "FAKA",
            "away_team": "FAKEB",
        }
        rows = compute_game_edges("SYNTH_00_FAKA_FAKEB", snapshot, prediction)
        assert len(rows) == 1
        (row,) = rows
        assert row["side"] == "home"
        assert row["edge"] == pytest.approx(0.10)
        assert row["model_version"] == "SYNTH-TEST"

    def test_compute_game_edges_never_invents_spread_total_edges(self):
        """Spread/total edges require per-market model probs; without them
        no rows are produced — the engine must not invent them."""
        _require_engine()
        snapshot = {
            "moneyline_home": -110,
            "moneyline_away": -110,
            "spread": -3.5,
            "spread_home_price": -110,
            "spread_away_price": -110,
            "total": 47.5,
            "total_over_price": -110,
            "total_under_price": -110,
        }
        prediction = {
            "game_id": "SYNTH_00_FAKA_FAKEB",
            "model_version": "SYNTH-TEST",
            # A coin-flip prediction: no moneyline edge either.
            "model_probability": 0.50,
            "predicted_winner": "FAKA",
            "home_team": "FAKA",
            "away_team": "FAKEB",
        }
        rows = compute_game_edges("SYNTH_00_FAKA_FAKEB", snapshot, prediction)
        assert rows == []


# ---------------------------------------------------------------------------
# Parlay/combo math — only if the engine provides it
# ---------------------------------------------------------------------------


def test_parlay_math_if_engine_has_it():
    _require_engine()
    candidates = ["parlay_probability", "parlay_ev", "combine_parlay", "parlay_edge"]
    found = [name for name in candidates if callable(getattr(_engine_module, name, None))]
    if not found:
        pytest.skip("engine has no parlay/combo math; skipping per spec")


# ---------------------------------------------------------------------------
# Provider: auth configuration degrades honestly without a key
# ---------------------------------------------------------------------------


class TestOddsProviderAuth:
    def test_auth_configured_false_with_no_api_key(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        provider = TheOddsApiProvider()
        assert provider.auth_configured() is False

    def test_auth_configured_true_with_api_key(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.setenv("ODDS_API_KEY", "synthetic-test-key")
        provider = TheOddsApiProvider()
        assert provider.auth_configured() is True

    def test_auth_configured_false_with_blank_api_key(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.setenv("ODDS_API_KEY", "   ")
        provider = TheOddsApiProvider()
        assert provider.auth_configured() is False


# ---------------------------------------------------------------------------
# Credit-budget guardrail (mongomock ledger, no network)
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_db():
    client = mongomock.MongoClient()
    return client["money_by_numbers_test"]


def _current_month():
    assert utcnow is not None
    return utcnow().strftime("%Y-%m")


class TestCreditBudget:
    def test_refuses_when_remaining_below_required(self, ledger_db):
        _require_odds_provider()
        ledger_db["odds_credit_usage"].insert_one(
            {"month": _current_month(), "used": 500, "remaining": 0}
        )
        ok, reason = check_budget(ledger_db, min_required=1)
        assert ok is False
        assert "0" in reason

    def test_refuses_when_remaining_less_than_required(self, ledger_db):
        _require_odds_provider()
        ledger_db["odds_credit_usage"].insert_one(
            {"month": _current_month(), "used": 495, "remaining": 5}
        )
        ok, _reason = check_budget(ledger_db, min_required=10)
        assert ok is False

    def test_refuses_when_balance_unknown(self, ledger_db):
        """No ledger record for the month -> refuse rather than pull blind."""
        _require_odds_provider()
        ok, reason = check_budget(ledger_db, min_required=1)
        assert ok is False
        assert "unknown" in reason.lower()

    def test_refuses_when_remaining_field_missing(self, ledger_db):
        _require_odds_provider()
        ledger_db["odds_credit_usage"].insert_one({"month": _current_month()})
        ok, _reason = check_budget(ledger_db, min_required=1)
        assert ok is False

    def test_accepts_when_remaining_covers_required(self, ledger_db):
        _require_odds_provider()
        ledger_db["odds_credit_usage"].insert_one(
            {"month": _current_month(), "used": 1, "remaining": 499}
        )
        ok, reason = check_budget(ledger_db, min_required=1)
        assert ok is True
        assert "499" in reason


# ---------------------------------------------------------------------------
# HTTP endpoints (TestClient; no DB configured -> honest degraded states)
# ---------------------------------------------------------------------------


class TestOddsEndpoints:
    def test_odds_status_no_key_returns_odds_unavailable(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        client = TestClient(_require_app())
        resp = client.get("/api/odds/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "odds_unavailable"
        assert "no API key" in body["reason"]

    def test_odds_status_with_key_no_db_reports_available(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.setenv("ODDS_API_KEY", "synthetic-test-key")
        monkeypatch.delenv("MONGODB_URI", raising=False)
        client = TestClient(_require_app())
        resp = client.get("/api/odds/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "odds_available"
        assert body["key_configured"] is True
        # No provider credit call and no DB: honest nulls, never invented.
        assert body["credits_remaining"] is None
        assert body["snapshot_count"] == 0

    def test_edges_no_db_returns_edges_unavailable(self, monkeypatch):
        _require_odds_provider()
        monkeypatch.delenv("MONGODB_URI", raising=False)
        client = TestClient(_require_app())
        resp = client.get("/api/edges")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "edges_unavailable"
        assert body["edges"] == []
        assert "database" in body["reason"]
