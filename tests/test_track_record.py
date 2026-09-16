"""Phase 6 track-record tests for MONEY BY NUMBERS.

Covers the immutable pick ledger (``backend/app/picks/ledger.py``), the
idempotent settlement engine (``backend/app/picks/settle.py``), the
track-record/methodology endpoints, the postgame pipeline job, and the
structural immutability guarantee (no write routes for ledger data).

HONESTY RULE: all fixtures here are SYNTHETIC (fake game ids, textbook
prices like -110) and are never presented as real market data. They exist
only to verify the math and the ledger discipline.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import ensure_indexes  # noqa: E402
from app.picks import (  # noqa: E402
    PickLedgerError,
    grade_pick,
    lock_pick,
    run_settlement,
)

UTC = timezone.utc


def _future_kickoff(hours: int = 48) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _past_kickoff(hours: int = 48) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    database = client["money_by_numbers"]
    ensure_indexes(database)
    return database


def _game(game_id="g1", status="final", home_score=27, away_score=20,
          week=1, home_team="KC", away_team="DEN"):
    return {
        "game_id": game_id,
        "season": 2026,
        "week": week,
        "home_team": home_team,
        "away_team": away_team,
        "game_date": _past_kickoff(72),
        "status": status,
        "home_score": home_score if status == "final" else None,
        "away_score": away_score if status == "final" else None,
    }


def _lock(db, pid="p1", game_id="g1", market="moneyline", side=None,
          prob=0.75, winner="KC", line=None, price=None):
    return lock_pick(
        db,
        prediction_id=pid,
        game_id=game_id,
        model_version="MNB-NFL-2026.01",
        training_cutoff="2025 season",
        model_probability=prob,
        predicted_winner=winner,
        kickoff=_future_kickoff(),
        season=2026,
        week=1,
        home_team="KC",
        away_team="DEN",
        predicted_home_score=27,
        predicted_away_score=20,
        market=market,
        side=side,
        market_line_at_pick=line,
        market_price_at_pick=price,
    )


# ---------------------------------------------------------------------------
# Ledger: lock_pick
# ---------------------------------------------------------------------------


class TestLockPick:
    def test_writes_valid_record(self, db):
        doc = _lock(db)
        assert doc["prediction_id"] == "p1"
        assert doc["confidence"] == "high"  # 0.75 >= 0.68
        assert db["predictions"].count_documents({}) == 1

    def test_confidence_tiers(self, db):
        assert _lock(db, pid="a", game_id="ga", prob=0.60)["confidence"] == "medium"
        assert _lock(db, pid="b", game_id="gb", prob=0.50)["confidence"] == "low"

    def test_refuses_duplicate_game_model_market(self, db):
        _lock(db)
        with pytest.raises(PickLedgerError, match="duplicate pick refused"):
            _lock(db, pid="p2")  # same game/model/market, new id

    def test_allows_different_market_same_game(self, db):
        _lock(db, market="moneyline")
        _lock(db, pid="p2", market="spread", side="home", line=-3.0)
        assert db["predictions"].count_documents({}) == 2

    def test_refuses_duplicate_prediction_id(self, db):
        _lock(db)
        with pytest.raises(PickLedgerError, match="duplicate prediction_id"):
            _lock(db, pid="p1", game_id="g2")

    def test_refuses_past_kickoff(self, db):
        with pytest.raises(PickLedgerError, match="not in the future"):
            lock_pick(
                db, prediction_id="late", game_id="g9",
                model_version="MNB-NFL-2026.01", training_cutoff="2025",
                model_probability=0.6, predicted_winner="KC",
                kickoff=_past_kickoff(1),
            )

    def test_refuses_missing_kickoff(self, db):
        with pytest.raises(PickLedgerError, match="without a kickoff"):
            lock_pick(
                db, prediction_id="nok", game_id="g9",
                model_version="MNB-NFL-2026.01", training_cutoff="2025",
                model_probability=0.6, predicted_winner="KC",
                kickoff=None,
            )

    def test_rejects_invalid_team(self, db):
        with pytest.raises(ValidationError):
            lock_pick(
                db, prediction_id="bad", game_id="g9",
                model_version="MNB-NFL-2026.01", training_cutoff="2025",
                model_probability=0.6, predicted_winner="XX",
                kickoff=_future_kickoff(),
            )


# ---------------------------------------------------------------------------
# Settlement grading: grade_pick (pure function)
# ---------------------------------------------------------------------------


def _pick(**over):
    base = {
        "prediction_id": "p1", "game_id": "g1", "model_version": "MNB-NFL-2026.01",
        "market": "moneyline", "side": "home", "confidence": "high",
        "model_probability": 0.75, "predicted_winner": "KC",
        "home_team": "KC", "away_team": "DEN",
        "market_line_at_pick": None, "market_price_at_pick": None,
    }
    base.update(over)
    return base


class TestGradePick:
    def test_moneyline_win(self):
        r = grade_pick(_pick(), _game(home_score=27, away_score=20))
        assert r["result"] == "win"
        assert r["brier_contribution"] == pytest.approx((0.75 - 1.0) ** 2)
        assert r["actual_winner"] == "KC"

    def test_moneyline_loss(self):
        r = grade_pick(_pick(predicted_winner="DEN", side="away"),
                       _game(home_score=27, away_score=20))
        assert r["result"] == "loss"
        assert r["brier_contribution"] == pytest.approx((0.75 - 0.0) ** 2)

    def test_moneyline_tie_is_push(self):
        r = grade_pick(_pick(), _game(home_score=20, away_score=20))
        assert r["result"] == "push"
        assert r["brier_contribution"] is None
        assert r["actual_winner"] is None

    def test_spread_home_covers(self):
        r = grade_pick(
            _pick(market="spread", side="home", market_line_at_pick=-3.0),
            _game(home_score=27, away_score=20),  # wins by 7
        )
        assert r["result"] == "win"

    def test_spread_push_on_exact_margin(self):
        r = grade_pick(
            _pick(market="spread", side="home", market_line_at_pick=-3.0),
            _game(home_score=23, away_score=20),  # wins by exactly 3
        )
        assert r["result"] == "push"

    def test_spread_home_fails_to_cover(self):
        r = grade_pick(
            _pick(market="spread", side="home", market_line_at_pick=-3.0),
            _game(home_score=21, away_score=20),  # wins by 1
        )
        assert r["result"] == "loss"

    def test_spread_away_covers(self):
        r = grade_pick(
            _pick(market="spread", side="away", market_line_at_pick=-3.0,
                  predicted_winner="DEN"),
            _game(home_score=21, away_score=20),  # home wins by 1, away +3 covers
        )
        assert r["result"] == "win"

    def test_spread_missing_line_is_ungraded(self):
        r = grade_pick(_pick(market="spread", side="home"), _game())
        assert r["result"] == "ungraded"

    def test_total_over_wins(self):
        r = grade_pick(
            _pick(market="total", side="over", market_line_at_pick=47.5),
            _game(home_score=27, away_score=24),  # 51 > 47.5
        )
        assert r["result"] == "win"

    def test_total_push_on_exact(self):
        r = grade_pick(
            _pick(market="total", side="under", market_line_at_pick=47.0),
            _game(home_score=27, away_score=20),  # exactly 47
        )
        assert r["result"] == "push"

    def test_total_under_loses_when_over(self):
        r = grade_pick(
            _pick(market="total", side="under", market_line_at_pick=40.5),
            _game(home_score=27, away_score=20),
        )
        assert r["result"] == "loss"

    def test_non_final_game_returns_none(self):
        assert grade_pick(_pick(), _game(status="scheduled")) is None


# ---------------------------------------------------------------------------
# run_settlement: idempotency, pending, CLV, audit
# ---------------------------------------------------------------------------


def _odds_snapshot(game_id, ts, **fields):
    doc = {"game_id": game_id, "timestamp": ts, "sportsbook": "synthetic-book"}
    doc.update(fields)
    return doc


class TestRunSettlement:
    def test_settles_moneyline_pick(self, db):
        db["games"].insert_one(_game())
        _lock(db)
        summary = run_settlement(db)
        assert summary["settled"] == 1
        assert summary["pending"] == 0
        res = db["pick_results"].find_one({"prediction_id": "p1"})
        assert res["result"] == "win"
        assert res["settled_at"] is not None
        # predictions ledger untouched: no result fields written back
        assert "result" not in db["predictions"].find_one({"prediction_id": "p1"})

    def test_idempotent_rerun(self, db):
        db["games"].insert_one(_game())
        _lock(db)
        first = run_settlement(db)
        second = run_settlement(db)
        assert db["pick_results"].count_documents({}) == 1
        assert first["settled"] == second["settled"] == 1
        res = db["pick_results"].find_one({"prediction_id": "p1"})
        assert res["result"] == "win"

    def test_pending_when_game_not_final(self, db):
        db["games"].insert_one(_game(status="scheduled"))
        _lock(db)
        summary = run_settlement(db)
        assert summary["settled"] == 0
        assert summary["pending"] == 1
        assert db["pick_results"].count_documents({}) == 0

    def test_writes_settlement_runs_audit(self, db):
        db["games"].insert_one(_game())
        _lock(db)
        summary = run_settlement(db, season=2026, week=1)
        audit = db["settlement_runs"].find_one({"run_id": summary["run_id"]})
        assert audit is not None
        assert audit["settled"] == 1

    def test_spread_clv_favorable(self, db):
        game = _game()
        db["games"].insert_one(game)
        _lock(db, pid="s1", market="spread", side="home", line=-3.0, price=-110)
        # Snapshots: pick-time line -3, later line moves to -5.5 (favorable).
        db["odds"].insert_many([
            _odds_snapshot("g1", game["game_date"] - timedelta(days=2),
                           spread=-3.0, spread_home_price=-110, spread_away_price=-110),
            _odds_snapshot("g1", game["game_date"] - timedelta(hours=1),
                           spread=-5.5, spread_home_price=-110, spread_away_price=-110),
        ])
        run_settlement(db)
        res = db["pick_results"].find_one({"prediction_id": "s1"})
        assert res["closing_line"] == -5.5
        assert res["clv_points"] == pytest.approx(-2.5)
        assert res["clv_favorable"] is True

    def test_spread_clv_unfavorable(self, db):
        game = _game()
        db["games"].insert_one(game)
        _lock(db, pid="s2", game_id="g2", market="spread", side="away",
              line=-3.0, price=-110, winner="DEN")
        db["games"].insert_one(_game(game_id="g2", week=2, home_score=27, away_score=20))
        db["odds"].insert_one(
            _odds_snapshot("g2", game["game_date"] - timedelta(hours=1),
                           spread=-5.5, spread_home_price=-110, spread_away_price=-110)
        )
        run_settlement(db)
        res = db["pick_results"].find_one({"prediction_id": "s2"})
        assert res["clv_points"] == pytest.approx(-2.5)
        assert res["clv_favorable"] is False  # line moved against the away side

    def test_moneyline_clv(self, db):
        game = _game()
        db["games"].insert_one(game)
        _lock(db, pid="m1", price=-150)  # picked KC at -150
        db["odds"].insert_one(
            _odds_snapshot("g1", game["game_date"] - timedelta(hours=1),
                           moneyline_home=-170, moneyline_away=150)
        )
        run_settlement(db)
        res = db["pick_results"].find_one({"prediction_id": "m1"})
        # -150 -> 0.6 implied; -170 -> ~0.6296 implied: steam in our favor.
        assert res["clv_prob_points"] == pytest.approx(170 / 270 - 150 / 250)
        assert res["clv_favorable"] is True

    def test_clv_none_without_snapshots(self, db):
        db["games"].insert_one(_game())
        _lock(db, pid="c1", market="spread", side="home", line=-3.0)
        run_settlement(db)
        res = db["pick_results"].find_one({"prediction_id": "c1"})
        assert res["closing_line"] is None
        assert res["clv_points"] is None
        assert res["clv_favorable"] is None


# ---------------------------------------------------------------------------
# Structural immutability: no write routes for ledger data
# ---------------------------------------------------------------------------


def _require_app():
    from app.main import app

    return app


class TestImmutability:
    @pytest.mark.parametrize("prefix", ["/api/predictions", "/api/track-record", "/api/methodology"])
    def test_no_write_routes_on_ledger_paths(self, prefix):
        app = _require_app()
        write_methods = {"PUT", "PATCH", "DELETE", "POST"}
        offenders = [
            (sorted(r.methods), r.path)
            for r in app.routes
            if hasattr(r, "path")
            and r.path.startswith(prefix)
            and write_methods.intersection(r.methods or set())
        ]
        assert offenders == [], f"write routes found on immutable ledger paths: {offenders}"


# ---------------------------------------------------------------------------
# Track-record endpoints (TestClient with monkeypatched _db)
# ---------------------------------------------------------------------------


def _client_with_db(db, monkeypatch):
    import app.routers.track_record as tr

    monkeypatch.setattr(tr, "_db", lambda: db)
    return TestClient(_require_app())


def _seed_results(db):
    """3 moneyline (2W/1L, one without price) + 2 spread (1W/1L)."""
    db["games"].insert_many([
        _game("g1", week=1, home_score=27, away_score=20),
        _game("g2", week=2, home_score=17, away_score=24),
        _game("g3", week=3, home_score=30, away_score=10),
        _game("g4", week=4, home_score=24, away_score=21),  # wins by 3
        _game("g5", week=5, home_score=14, away_score=28),
    ])
    _lock(db, pid="w1", game_id="g1", prob=0.75, price=-110)          # win, high
    _lock(db, pid="l1", game_id="g2", prob=0.72, price=-110)          # loss, high
    _lock(db, pid="w2", game_id="g3", prob=0.55)                      # win, low, no price
    _lock(db, pid="s1", game_id="g4", market="spread", side="home",
          line=-3.0, prob=0.62, price=-110)                           # push (won by exactly 3)
    _lock(db, pid="s2", game_id="g5", market="spread", side="home",
          line=-3.0, prob=0.65, price=-110, winner="KC")               # loss
    run_settlement(db)


class TestTrackRecordEndpoints:
    def test_summary_no_db(self, monkeypatch):
        import app.routers.track_record as tr

        monkeypatch.setattr(tr, "_db", lambda: None)
        r = TestClient(_require_app()).get("/api/track-record/summary")
        assert r.status_code == 200
        assert r.json()["status"] == "no_data"

    def test_summary_empty_db(self, db, monkeypatch):
        r = _client_with_db(db, monkeypatch).get("/api/track-record/summary")
        body = r.json()
        assert body["status"] == "no_settled_picks"
        assert body["summary"] is None

    def test_summary_math_from_ledger(self, db, monkeypatch):
        _seed_results(db)
        body = _client_with_db(db, monkeypatch).get("/api/track-record/summary").json()
        assert body["status"] == "ok"
        s = body["summary"]
        # 2 moneyline wins + 1 moneyline loss; spread: 1 push + 1 loss.
        assert s["wins"] == 2
        assert s["losses"] == 2
        assert s["pushes"] == 1
        assert s["accuracy"] == pytest.approx(0.5)
        # ROI over priced countable picks only: w1(+10/11), l1(-1), s2(-1).
        assert s["roi_n"] == 3
        assert s["roi_excluded_no_price"] == 1  # w2 had no price
        assert s["roi_units"] == pytest.approx(2 * 0 + 10 / 11 - 1 - 1 + 0)
        # by-market split is computed, not hardcoded.
        assert body["by_market"]["moneyline"]["wins"] == 2
        assert body["by_market"]["spread"]["pushes"] == 1

    def test_tiers(self, db, monkeypatch):
        _seed_results(db)
        tiers = _client_with_db(db, monkeypatch).get("/api/track-record/tiers").json()["tiers"]
        assert tiers["HIGH"]["accuracy"] == pytest.approx(0.5)   # w1 win, l1 loss
        assert tiers["HIGH"]["n"] == 2
        assert tiers["LOW"]["accuracy"] == pytest.approx(1.0)    # w2 win
        assert tiers["MEDIUM"]["n"] == 2  # s1 push, s2 loss -> accuracy over decided: 0/1

    def test_tiers_empty(self, db, monkeypatch):
        body = _client_with_db(db, monkeypatch).get("/api/track-record/tiers").json()
        assert body["status"] == "no_settled_picks"

    def test_calibration_buckets(self, db, monkeypatch):
        _seed_results(db)
        body = _client_with_db(db, monkeypatch).get("/api/track-record/calibration").json()
        assert body["status"] == "ok"
        buckets = {b["prob_low"]: b for b in body["buckets"]}
        # 0.7 bucket: w1 (0.75 win), l1 (0.72 loss) -> observed 0.5
        b = buckets[0.7]
        assert b["n"] == 2
        assert b["observed_win_rate"] == pytest.approx(0.5)
        assert b["avg_predicted"] == pytest.approx((0.75 + 0.72) / 2)
        # 0.5 bucket: w2 (0.55 win) -> observed 1.0
        assert buckets[0.5]["observed_win_rate"] == pytest.approx(1.0)

    def test_picks_lists_pending_and_settled(self, db, monkeypatch):
        _seed_results(db)
        db["games"].insert_one(_game("g6", week=6, status="scheduled"))
        _lock(db, pid="pend1", game_id="g6", prob=0.66)
        picks = _client_with_db(db, monkeypatch).get("/api/track-record/picks").json()["picks"]
        by_id = {p["prediction_id"]: p for p in picks}
        assert by_id["pend1"]["settlement_status"] == "pending"
        assert by_id["w1"]["settlement_status"] == "settled"
        assert by_id["w1"]["result"] == "win"
        assert by_id["l1"]["result"] == "loss"

    def test_picks_result_filter(self, db, monkeypatch):
        _seed_results(db)
        picks = _client_with_db(db, monkeypatch).get(
            "/api/track-record/picks?result=win").json()["picks"]
        assert {p["prediction_id"] for p in picks} == {"w1", "w2"}

    def test_methodology_reads_artifacts(self, monkeypatch):
        import app.routers.track_record as tr

        monkeypatch.setattr(tr, "_db", lambda: None)
        body = TestClient(_require_app()).get("/api/methodology").json()
        assert body["status"] == "ok"
        wf = body["walk_forward"]
        # Numbers must match the real on-disk artifacts — read independently.
        meta = json.loads(
            (tr._ARTIFACTS_DIR / "MNB-NFL-2026.02" / "metadata.json").read_text()
        )
        assert wf["ensemble_accuracy"] == pytest.approx(meta["walk_forward"]["accuracy"])
        assert wf["elo_baseline_accuracy"] == pytest.approx(meta["walk_forward"]["elo_accuracy"])
        assert wf["n_games"] == 2639
        assert wf["leakage_harness"] == "passed"
        assert len(wf["per_season"]) == 10
        # The honest headline: ensemble did NOT beat Elo.
        assert wf["ensemble_accuracy"] < wf["elo_baseline_accuracy"]
        # Research note: honestly pending until the parallel research task
        # delivers METHODOLOGY_PUBLIC.md, then served verbatim from disk.
        research_path = Path(__file__).resolve().parent.parent.parent / (
            "nfl-model-research/METHODOLOGY_PUBLIC.md"
        )
        if research_path.exists():
            assert body["research_note"] == research_path.read_text()
            assert body["research_note_status"] == "available"
        else:
            assert body["research_note"] is None
            assert "pending" in body["research_note_status"]


# ---------------------------------------------------------------------------
# Postgame pipeline job
# ---------------------------------------------------------------------------


class TestPostgameJob:
    def test_run_with_db_succeeds(self, db):
        from pipelines.jobs.postgame import PostgameJob
        from pipelines.scheduler import JobStatus

        db["games"].insert_one(_game())
        _lock(db)
        record = PostgameJob().run({"db": db})
        assert record.status == JobStatus.SUCCEEDED
        assert db["pick_results"].count_documents({}) == 1

    def test_run_without_db_fails_honestly(self, monkeypatch):
        from pipelines.jobs.postgame import PostgameJob
        from pipelines.scheduler import JobStatus

        monkeypatch.delenv("MONGODB_URI", raising=False)
        record = PostgameJob().run({"db": None})
        assert record.status == JobStatus.FAILED
        assert "no database" in (record.error or "")
