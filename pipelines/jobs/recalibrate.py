"""In-season recalibration job (Phase 2) — spec sections 17-19.

Elo updates after every game, so team strength was never "static". This job
adds the two pieces that were missing:

1. WEEKLY probability-calibration refit (every Wednesday, in season only).
   Elo probabilities are systematically overconfident — 2016-2025
   walk-forward: mean implied 0.6775 vs actual favorite win rate 0.6218
   (n=2639). The job fits Platt scaling ``p_cal = expit(a + b*logit(p_raw))``
   on the current season's settled picks and upserts it into the
   ``calibration`` collection. The prediction path reads the latest doc;
   the contract is documented in docs/IN_SEASON_RECALIBRATION.md.
   Fewer than 50 settled picks -> honest SkipJob (no fit on noise).

2. FOUR-WEEK factor review. Every 4th successful refit, the job re-fits
   (a, b) on all-but-the-last-4-weeks, evaluates candidate vs incumbent on
   the held-out last 4 weeks, and only adopts the candidate when it beats
   the incumbent's held-out Brier by at least ``BRIER_MARGIN``. Otherwise
   the candidate is REJECTED and the incumbent stands. The verdict is
   recorded in ``factor_reviews`` — acceptances and rejections alike.

Guardrails (same philosophy as the retrain job):
- The job NEVER promotes a model. It snapshots the champion before and
  after and fails loudly if the champion document changed.
- The job never fabricates: no settled picks -> skip; no held-out games
  on a review week -> the review is recorded as "no_held_out_data".
- The situational discount (dead-rubber) is re-measured on review weeks
  and reported, but only changes through the same held-out guardrail
  with n >= 8 new dead-rubber games — in practice it will almost always
  be reported as "unchanged (insufficient new data)".
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pipelines.jobs._common import require_db, utcnow
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

#: Minimum settled picks before a calibration fit is attempted.
MIN_SETTLED_PICKS = 50
#: Held-out Brier improvement required to adopt a candidate (a, b).
BRIER_MARGIN = 0.002
#: Review cadence: every Nth successful weekly refit.
REVIEW_EVERY = 4
#: Held-out window for the review, in weeks.
HELD_OUT_WEEKS = 4
#: New dead-rubber games required before the discount may change.
MIN_NEW_DEAD_RUBBER = 8


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-float(x)))


def platt_fit(probs: List[float], outcomes: List[int]) -> Tuple[float, float]:
    """Fit ``outcome ~ expit(a + b*logit(p))`` by IRLS (tiny ridge).

    Returns (a, b). Pure numpy; deterministic. Raises ValueError on empty
    input or degenerate (all-same-outcome) data.
    """
    import numpy as np

    if len(probs) != len(outcomes) or not probs:
        raise ValueError("platt_fit needs non-empty aligned inputs")
    y = np.asarray(outcomes, dtype=float)
    if y.min() == y.max():
        raise ValueError("platt_fit needs both outcomes present")
    x = np.array([_logit(p) for p in probs], dtype=float)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    ridge = 1e-6
    for _ in range(100):
        lin = X @ beta
        mu = 1.0 / (1.0 + np.exp(-lin))
        w = mu * (1 - mu) + 1e-9
        z = lin + (y - mu) / w
        XtWX = (X.T * w) @ X + ridge * np.eye(2)
        XtWz = (X.T * w) @ z
        new_beta = np.linalg.solve(XtWX, XtWz)
        if np.max(np.abs(new_beta - beta)) < 1e-10:
            beta = new_beta
            break
        beta = new_beta
    return float(beta[0]), float(beta[1])


def brier_score(probs: List[float], outcomes: List[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def calibrate(p: float, a: float, b: float) -> float:
    """Apply stored Platt params: ``expit(a + b*logit(p))``."""
    return _expit(a + b * _logit(p))


def _season_settled_picks(db: Any, season: int) -> List[Dict[str, Any]]:
    cur = db["predictions"].find(
        {"season": season, "correct": {"$ne": None}},
        {"_id": 0, "model_probability": 1, "correct": 1, "week": 1,
         "adjustments": 1, "game_id": 1},
    )
    picks = []
    for doc in cur:
        try:
            p = float(doc["model_probability"])
            y = 1 if doc["correct"] else 0
            picks.append({"p": p, "y": y, "week": doc.get("week"),
                          "game_id": doc.get("game_id"),
                          "adjustments": doc.get("adjustments") or []})
        except (KeyError, TypeError, ValueError):
            continue
    return picks


def _champion_snapshot(db: Any) -> Optional[Dict[str, Any]]:
    return db["models"].find_one({"champion": True}, {"_id": 0})


def _champions_equal(a: Any, b: Any) -> bool:
    return a == b


class RecalibrateJob(Job):
    """Weekly in-season calibration refit + 4-weekly factor review."""

    name = "recalibrate"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record
        now = context.get("now") or utcnow()

        champ_before = _champion_snapshot(db)

        season = context.get("season")
        if season is None:
            latest = db["predictions"].find_one(
                {"season": {"$ne": None}}, sort=[("season", -1)],
                projection={"_id": 0, "season": 1})
            if not latest:
                raise SkipJob("no predictions in the ledger — nothing to calibrate")
            season = int(latest["season"])

        picks = _season_settled_picks(db, int(season))
        if len(picks) < MIN_SETTLED_PICKS:
            raise SkipJob(
                f"only {len(picks)} settled picks for season {season} "
                f"(need {MIN_SETTLED_PICKS}) — no fit on noise"
            )

        probs = [d["p"] for d in picks]
        outcomes = [d["y"] for d in picks]
        a_new, b_new = platt_fit(probs, outcomes)

        cal = db["calibration"].find_one({"season": int(season)}, {"_id": 0}) or {}
        refit_count = int(cal.get("refit_count", 0)) + 1

        detail: Dict[str, Any] = {
            "season": int(season),
            "n_settled": len(picks),
            "weekly_fit": {"a": round(a_new, 4), "b": round(b_new, 4)},
            "refit_count": refit_count,
        }

        # --- 4-week factor review ---
        # On review weeks ANY change to the live params is a candidate
        # change: it goes through the held-out guardrail, otherwise the
        # incumbent stands. On ordinary weeks the weekly fit is the policy.
        review_due = refit_count % REVIEW_EVERY == 0
        review: Dict[str, Any] = {"due": review_due}
        if review_due:
            verdict = self._four_week_review(db, int(season), picks, cal, now)
            review.update(verdict)
            if verdict["decision"] == "candidate_accepted":
                live_a, live_b = verdict["a_full"], verdict["b_full"]
                live_n = verdict["n_train"]
                live_note = ("4-week review ACCEPTED the held-out-train fit; "
                             "live params rest on the train split.")
            else:
                live_a = float(cal.get("a", 0.0))
                live_b = float(cal.get("b", 1.0))
                live_n = int(cal.get("n", 0))
                live_note = ("4-week review did not accept a candidate "
                             f"({verdict['decision']}); incumbent kept.")
            detail["live_params_source"] = "four_week_review"
        else:
            live_a, live_b, live_n = a_new, b_new, len(picks)
            live_note = ("Weekly Platt re-fit on season-to-date settled picks. "
                         "The 4-week review is the only mid-stream replacement "
                         "path, and only via the held-out guardrail.")
            detail["live_params_source"] = "weekly_refit"

        doc = {
            "season": int(season),
            "fitted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n": live_n,
            "a": live_a,
            "b": live_b,
            "refit_count": refit_count,
            "source": "predictions ledger + settlements (walk-forward: past games only)",
            "contract": "prediction path applies p_cal = expit(a + b*logit(p_raw))",
            "note": live_note,
        }
        db["calibration"].update_one({"season": int(season)}, {"$set": doc}, upsert=True)
        detail["four_week_review"] = review
        detail["live"] = {"a": round(live_a, 4), "b": round(live_b, 4)}

        # Champion guard: the scheduler never promotes.
        champ_after = _champion_snapshot(db)
        if not _champions_equal(champ_before, champ_after):
            record.finish(JobStatus.FAILED,
                          error="champion document changed during recalibrate — refusing")
            record.detail = detail
            return record

        record.detail = detail
        record.finish(JobStatus.SUCCEEDED)
        return record

    def _four_week_review(
        self,
        db: Any,
        season: int,
        picks: List[Dict[str, Any]],
        prev_cal: Dict[str, Any],
        now: Any,
    ) -> Dict[str, Any]:
        """Held-out review of candidate calibration params.

        Candidate (a,b) is fit on all-but-the-last-4-weeks; incumbent is the
        previously stored (a,b) (or identity when none exists yet).
        Returns a verdict dict; the caller decides the live params.
        Adopted only if the candidate's held-out Brier beats the
        incumbent's by >= BRIER_MARGIN. Verdict recorded either way.
        """
        weeks = sorted({d["week"] for d in picks if d.get("week")})
        verdict: Dict[str, Any] = {"held_out_weeks": HELD_OUT_WEEKS,
                                   "margin": BRIER_MARGIN}
        if len(weeks) <= HELD_OUT_WEEKS:
            verdict.update({"decision": "no_held_out_data",
                            "reason": "fewer than 5 distinct settled weeks"})
            self._record_review(db, season, verdict, now)
            return verdict

        cutoff_weeks = set(weeks[-HELD_OUT_WEEKS:])
        train = [d for d in picks if d.get("week") not in cutoff_weeks]
        held = [d for d in picks if d.get("week") in cutoff_weeks]
        if len(train) < MIN_SETTLED_PICKS or not held:
            verdict.update({"decision": "no_held_out_data",
                            "reason": "insufficient train/held-out split"})
            self._record_review(db, season, verdict, now)
            return verdict

        try:
            a_cand, b_cand = platt_fit([d["p"] for d in train],
                                       [d["y"] for d in train])
        except ValueError as exc:
            verdict.update({"decision": "candidate_rejected",
                            "reason": f"candidate fit failed: {exc}"})
            self._record_review(db, season, verdict, now)
            return verdict

        a_inc = float(prev_cal.get("a", 0.0))
        b_inc = float(prev_cal.get("b", 1.0))
        hp = [d["p"] for d in held]
        hy = [d["y"] for d in held]
        brier_cand = brier_score([calibrate(p, a_cand, b_cand) for p in hp], hy)
        brier_inc = brier_score([calibrate(p, a_inc, b_inc) for p in hp], hy)
        improvement = brier_inc - brier_cand
        verdict.update({
            "n_train": len(train),
            "n_held_out": len(held),
            "brier_candidate": round(brier_cand, 4),
            "brier_incumbent": round(brier_inc, 4),
            "improvement": round(improvement, 5),
        })
        if improvement >= BRIER_MARGIN:
            verdict.update({"decision": "candidate_accepted",
                            "a": round(a_cand, 4), "b": round(b_cand, 4),
                            "a_full": a_cand, "b_full": b_cand})
        else:
            verdict.update({"decision": "candidate_rejected",
                            "reason": (f"held-out Brier improvement {improvement:.5f} "
                                       f"< margin {BRIER_MARGIN}")})
        verdict["dead_rubber"] = self._remeasure_dead_rubber(picks)
        self._record_review(db, season, verdict, now)
        return verdict

    def _remeasure_dead_rubber(
        self, picks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Re-measure the dead-rubber discount on season-to-date picks.

        Reported always; actionable only with >= MIN_NEW_DEAD_RUBBER new
        tagged games AND the held-out guardrail — in practice almost always
        "unchanged (insufficient new data)".
        """
        tagged = [d for d in picks if "situational-adjustment" in d["adjustments"]]
        n = len(tagged)
        out: Dict[str, Any] = {"n_tagged_games": n,
                               "min_required": MIN_NEW_DEAD_RUBBER}
        if n < MIN_NEW_DEAD_RUBBER:
            out["decision"] = "unchanged (insufficient new data)"
            return out
        out["decision"] = "re-estimate available — requires held-out guardrail pass"
        out["observed_win_rate"] = round(sum(d["y"] for d in tagged) / n, 3)
        out["mean_model_prob"] = round(sum(d["p"] for d in tagged) / n, 3)
        return out

    def _record_review(self, db: Any, season: int, verdict: Dict[str, Any],
                       now: Any) -> None:
        db["factor_reviews"].insert_one({
            "season": season,
            "reviewed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job": self.name,
            **verdict,
        })
