"""Phase 2 tests: stakes classifier, situational adjustment, recalibration job.

The measurement behind these tests lives in docs/IN_SEASON_RECALIBRATION.md
(2016-2025 walk-forward, n=2639, no leakage). Only the dead-rubber
discount earned inclusion; every other stakes effect was tested and
excluded — the tests below assert that ONLY dead-rubber shifts the number.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock
import pytest

from pipelines.jobs.recalibrate import (
    RecalibrateJob,
    brier_score,
    calibrate,
    platt_fit,
)
from pipelines.schedule import JOB_SCHEDULES
from pipelines.scheduler import JobStatus

from app.situational import DEAD_RUBBER_DISCOUNT_ELO


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

NFC_2016_W17 = {
    # (wins, losses) before Week 17, 2016 (16-game season, 6 slots).
    "DAL": (13, 2), "NYG": (10, 5), "DET": (9, 6), "ATL": (10, 5),
    "SEA": (9, 5), "GB": (9, 6), "WAS": (8, 6), "TB": (8, 7),
    "MIN": (7, 8), "ARI": (6, 8), "PHI": (6, 9), "NO": (7, 8),
    "CAR": (6, 9), "LAR": (4, 11), "CHI": (3, 12), "SF": (1, 14),
}


def _records(table, conference="NFC"):
    return {
        t: {"wins": w, "losses": l, "ties": 0, "conference": conference}
        for t, (w, l) in table.items()
    }


@pytest.fixture()
def db():
    return mongomock.MongoClient()["money_by_numbers"]


# ---------------------------------------------------------------------------
# stakes classifier
# ---------------------------------------------------------------------------

class TestStakesClassifier:
    def test_2016_w17_dal_is_dead_rubber(self):
        from app.situational.stakes import classify_stakes

        r = classify_stakes("DAL", "PHI", _records(NFC_2016_W17), 16, 17,
                            playoff_slots=6)
        assert r["stakes"] == "DEAD_RUBBER"
        assert "seed locked" in r["reason"]

    def test_2016_w17_phi_is_eliminated(self):
        from app.situational.stakes import classify_stakes

        r = classify_stakes("PHI", "DAL", _records(NFC_2016_W17), 16, 17,
                            playoff_slots=6)
        assert r["stakes"] == "ELIMINATED"

    def test_early_season_is_normal(self):
        from app.situational.stakes import classify_stakes

        table = {t: (1, 1) for t in NFC_2016_W17}
        r = classify_stakes("DAL", "PHI", _records(table), 16, 3,
                            playoff_slots=6)
        assert r["stakes"] == "NORMAL"

    def test_win_and_in_is_must_win(self):
        from app.situational.stakes import classify_stakes

        # A (8-7) vs B (8-7), five rivals at 9-6, everyone else 5-10.
        # Before: 6 foes have max_pts > 8 -> not clinched. If A beats B,
        # A reaches 9 and only the five rivals can finish above 9 -> clinches.
        table = {t: (5, 10) for t in NFC_2016_W17}
        table["A"] = (8, 7)
        table["B"] = (8, 7)
        for i in range(1, 6):
            table[f"R{i}"] = (9, 6)
        rec = _records(table)
        r = classify_stakes("A", "B", rec, 16, 17, playoff_slots=6)
        assert r["stakes"] == "MUST_WIN"
        assert "win-and-in" in r["reason"]

    def test_lose_and_out_is_must_win(self):
        from app.situational.stakes import classify_stakes

        # Team A (8-7), six rivals at 9-6: a loss leaves A catchable by a
        # 7th rival at 8-7 -> eliminated; a win keeps A alive.
        table = {t: (8, 7) for t in NFC_2016_W17}
        table["A"] = (8, 7)
        table["B"] = (5, 10)
        rivals = ["R1", "R2", "R3", "R4", "R5", "R6"]
        for i, rname in enumerate(rivals):
            table[rname] = (9, 6)
        rec = _records(table)
        r = classify_stakes("A", "B", rec, 16, 17, playoff_slots=6)
        assert r["stakes"] == "MUST_WIN"
        assert "lose-and-out" in r["reason"]

    def test_detail_carries_audit_math(self):
        from app.situational.stakes import classify_stakes

        r = classify_stakes("DAL", "PHI", _records(NFC_2016_W17), 16, 17,
                            playoff_slots=6)
        d = r["detail"]
        assert d["pts"] == 13.0
        assert d["max_pts"] == 14.0
        assert d["seed_locked"] is True
        assert d["eliminated"] is False

    def test_bad_inputs_raise(self):
        from app.situational.stakes import classify_stakes

        rec = _records(NFC_2016_W17)
        with pytest.raises(ValueError):
            classify_stakes("DAL", "XXX", rec, 16, 17)
        with pytest.raises(ValueError):
            classify_stakes("DAL", "PHI", rec, 0, 17)
        with pytest.raises(ValueError):
            classify_stakes("DAL", "PHI", rec, 16, 17, playoff_slots=9)


# ---------------------------------------------------------------------------
# situational adjustment
# ---------------------------------------------------------------------------

class TestSituationalAdjustment:
    def test_discount_is_shrunken_fifty(self):
        # Raw MLE ~349 Elo (n=8) shrunk 8/(8+48) -> 50.0. If this number
        # changes, the research doc and the derivation comment must change too.
        assert DEAD_RUBBER_DISCOUNT_ELO == 50.0

    def test_dead_rubber_home_shifts_and_tags(self):
        from app.situational.adjustment import SITUATIONAL_TAG, situational_shifts

        s = situational_shifts("DEAD_RUBBER", "MUST_WIN")
        assert s["shift_home"] == -50.0
        assert s["shift_away"] == 0.0
        assert s["tags"] == [SITUATIONAL_TAG]
        assert SITUATIONAL_TAG == "situational-adjustment"
        applied = s["detail"][SITUATIONAL_TAG]
        assert len(applied) == 1
        assert applied[0]["team_side"] == "home"
        assert applied[0]["elo_shift"] == -50.0
        assert "basis" in applied[0]

    def test_dead_rubber_away_shifts_and_tags(self):
        from app.situational.adjustment import situational_shifts

        s = situational_shifts("NORMAL", "DEAD_RUBBER")
        assert s["shift_home"] == 0.0
        assert s["shift_away"] == -50.0
        assert s["tags"] == ["situational-adjustment"]

    def test_both_dead_rubber_nets_zero_but_stays_tagged(self):
        from app.situational.adjustment import situational_shifts

        s = situational_shifts("DEAD_RUBBER", "DEAD_RUBBER")
        assert s["shift_home"] == s["shift_away"] == -50.0
        assert s["tags"] == ["situational-adjustment"]
        assert len(s["detail"]["situational-adjustment"]) == 2

    def test_excluded_categories_never_shift(self):
        # MUST_WIN / ELIMINATED / NORMAL were tested and excluded: the
        # adjustment must be exactly zero and carry no tag.
        from app.situational.adjustment import situational_shifts

        for home in ("MUST_WIN", "ELIMINATED", "NORMAL"):
            for away in ("MUST_WIN", "ELIMINATED", "NORMAL"):
                s = situational_shifts(home, away)
                assert s["shift_home"] == 0.0
                assert s["shift_away"] == 0.0
                assert s["tags"] == []
                assert s["detail"] == {}

    def test_zero_shift_is_identity(self):
        from app.situational.adjustment import adjusted_win_probability

        # Matches the standard Elo logistic exactly.
        p = adjusted_win_probability(1600, 1500, 0.0, 0.0)
        expected = 1.0 / (1.0 + 10.0 ** (-(100 + 35) / 400.0))
        assert p == pytest.approx(expected)

    def test_shift_moves_probability_in_right_direction(self):
        from app.situational.adjustment import adjusted_win_probability

        raw = adjusted_win_probability(1600, 1500, 0.0, 0.0)
        adj = adjusted_win_probability(1600, 1500, -50.0, 0.0)
        assert adj < raw  # dead-rubber home team is weaker than rated

    def test_invalid_stakes_raise(self):
        from app.situational.adjustment import situational_shifts

        with pytest.raises(ValueError):
            situational_shifts("RESTING", "NORMAL")


# ---------------------------------------------------------------------------
# platt calibration math
# ---------------------------------------------------------------------------

class TestPlattCalibration:
    def test_fit_recovers_shrinkage(self):
        import random

        rng = random.Random(42)
        # Overconfident forecaster: true p = 0.5 + 0.5*(q-0.5).
        probs, outcomes = [], []
        for _ in range(2000):
            q = rng.choice([0.6, 0.7, 0.8, 0.9])
            true_p = 0.5 + 0.5 * (q - 0.5)
            probs.append(q)
            outcomes.append(1 if rng.random() < true_p else 0)
        a, b = platt_fit(probs, outcomes)
        assert b < 1.0  # shrinks toward 0.5
        assert b > 0.0
        cal = [calibrate(p, a, b) for p in probs]
        assert brier_score(cal, outcomes) < brier_score(probs, outcomes)

    def test_fit_rejects_degenerate(self):
        with pytest.raises(ValueError):
            platt_fit([0.6, 0.7], [1, 1])
        with pytest.raises(ValueError):
            platt_fit([], [])

    def test_calibrate_identity_params(self):
        assert calibrate(0.7, 0.0, 1.0) == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# recalibrate job
# ---------------------------------------------------------------------------

def _seed_picks(db, season, n, start_week=1, prob=0.65, win_rate=0.6, tag=False):
    import random

    rng = random.Random(7)
    for i in range(n):
        p = prob
        won = rng.random() < win_rate
        db["predictions"].insert_one({
            "prediction_id": f"P-{season}-{i}",
            "game_id": f"G-{season}-{i}",
            "season": season,
            "week": start_week + (i // 16),
            "model_probability": p,
            "predicted_winner": "HOME",
            "correct": won,
            "adjustments": ["situational-adjustment"] if tag else [],
        })


class TestRecalibrateJob:
    def test_skips_honestly_below_minimum(self, db):
        from pipelines.scheduler import SkipJob

        _seed_picks(db, 2026, 20)
        with pytest.raises(SkipJob, match="need 50"):
            RecalibrateJob().run({"db": db, "season": 2026})
        assert db["calibration"].count_documents({}) == 0

    def test_weekly_refit_writes_calibration(self, db):
        _seed_picks(db, 2026, 80)
        rec = RecalibrateJob().run({"db": db, "season": 2026})
        assert rec.status == JobStatus.SUCCEEDED
        doc = db["calibration"].find_one({"season": 2026})
        assert doc is not None
        assert doc["n"] == 80
        assert doc["refit_count"] == 1
        assert doc["b"] < 1.0  # synthetic forecaster is overconfident
        assert rec.detail["four_week_review"]["due"] is False

    def test_champion_guard_blocks_on_change(self, db):
        _seed_picks(db, 2026, 80)
        db["models"].insert_one({"version": "MNB-NFL-2026.01", "champion": True})
        job = RecalibrateJob()
        # Simulate a champion change mid-run by patching _champion_snapshot.
        import pipelines.jobs.recalibrate as rc

        calls = {"n": 0}
        orig = rc._champion_snapshot

        def flip(db_):
            calls["n"] += 1
            snap = orig(db_)
            if calls["n"] == 2:
                return {"version": "MNB-NFL-2026.02", "champion": True}
            return snap

        rc._champion_snapshot = flip
        try:
            rec = job.run({"db": db, "season": 2026})
        finally:
            rc._champion_snapshot = orig
        assert rec.status == JobStatus.FAILED
        assert "champion" in (rec.error or "")

    def test_fourth_run_rejects_weak_candidate(self, db):
        # Incumbent calibration is already perfect on this data and the
        # sample is large: the review candidate cannot beat it by the
        # margin -> rejected, incumbent stands, verdict recorded.
        import random

        rng = random.Random(11)
        n = 2000
        for i in range(n):
            p = rng.choice([0.55, 0.65, 0.75])
            db["predictions"].insert_one({
                "prediction_id": f"Q-2026-{i}",
                "game_id": f"H-2026-{i}",
                "season": 2026,
                "week": 1 + (i // 250),  # 8 distinct weeks
                "model_probability": p,
                "predicted_winner": "HOME",
                "correct": rng.random() < p,  # well-calibrated forecaster
            })
        db["calibration"].insert_one({
            "season": 2026, "a": 0.0, "b": 1.0, "n": n, "refit_count": 3,
        })
        rec = RecalibrateJob().run({"db": db, "season": 2026})
        assert rec.status == JobStatus.SUCCEEDED
        review = rec.detail["four_week_review"]
        assert review["due"] is True
        assert review["decision"] == "candidate_rejected"
        assert "margin" in review["reason"]
        # Rejections are recorded, not silent; the incumbent stands.
        assert db["factor_reviews"].count_documents({"season": 2026}) == 1
        doc = db["calibration"].find_one({"season": 2026})
        assert doc["a"] == 0.0 and doc["b"] == 1.0

    def test_dead_rubber_remeasure_reports_insufficient_data(self, db):
        _seed_picks(db, 2026, 80, tag=True)  # 80 tagged but review needs split
        db["calibration"].insert_one({
            "season": 2026, "a": 0.0, "b": 1.0, "n": 0, "refit_count": 3,
        })
        rec = RecalibrateJob().run({"db": db, "season": 2026})
        review = rec.detail["four_week_review"]
        if review.get("dead_rubber"):
            assert review["dead_rubber"]["n_tagged_games"] == 80
            assert "insufficient" in review["dead_rubber"]["decision"] or \
                "re-estimate" in review["dead_rubber"]["decision"]


# ---------------------------------------------------------------------------
# schedule wiring
# ---------------------------------------------------------------------------

class TestRecalibrateSchedule:
    def test_recalibrate_is_scheduled_weekly_in_season(self):
        entry = next(e for e in JOB_SCHEDULES if e["name"] == "recalibrate")
        assert entry["job_path"] == "pipelines.jobs.recalibrate:RecalibrateJob"
        assert entry["cadence"]["kind"] == "weekly_days"
        assert entry["cadence"]["days"] == [2]  # Wednesday
        assert entry["in_season_only"] is True
