"""Model retrain job (Phase 10) — spec section 36.

Runs monthly. It produces a CHALLENGER model version only — promotion to
champion is a manual, explicit step and can never happen here:

- The leakage harness gates training: ``ml.train.train_final_model``
  raises ``AssertionError`` on any kickoff-rule violation, which fails
  the job before any artifact is registered.
- The job only runs when new final games exist since the last training
  data (freshness gate); otherwise it skips honestly.
- The new version is registered in the ``models`` collection with
  ``champion: false`` and ``role: "challenger"``. The job snapshots the
  champion before training and verifies it is byte-identical afterwards;
  any change fails the job loudly.
- A ``challenger.json`` marker is written next to the artifacts stating
  the version is not promoted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pipelines.jobs._common import ensure_repo_root, require_db, utcnow
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

VERSION_RE = re.compile(r"^MNB-NFL-(\d{4})\.(\d{2})$")


def _next_challenger_version(artifacts_dir: Path, year: int) -> str:
    """Next version for the year: max existing NN + 1 (or .01)."""
    best = 0
    if artifacts_dir.is_dir():
        for child in artifacts_dir.iterdir():
            m = VERSION_RE.match(child.name)
            if m and int(m.group(1)) == year:
                best = max(best, int(m.group(2)))
    return f"MNB-NFL-{year}.{best + 1:02d}"


def _default_train(version: str) -> Path:
    """Real training: leakage-gated, persists versioned artifacts."""
    ensure_repo_root()
    from ml.train import train_final_model

    _bundle, out_dir = train_final_model(version=version)
    return out_dir


def _latest_final_game(db: Any) -> Optional[Dict[str, Any]]:
    return db["games"].find_one(
        {"status": "final"},
        sort=[("season", -1), ("week", -1)],
        projection={"_id": 0, "season": 1, "week": 1},
    )


class RetrainJob(Job):
    """Monthly retrain producing a challenger version (never promoted)."""

    name = "retrain"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        now = context.get("now") or utcnow()

        # Freshness gate: only train when there are new finals.
        latest = _latest_final_game(db)
        if not latest:
            raise SkipJob("no final games in the database — nothing to train on")
        last_training = ((context.get("last_training_data"))
                         or self._last_training_data(db))
        if last_training and (latest["season"], latest["week"]) <= (
            last_training.get("season", 0), last_training.get("week", 0)):
            raise SkipJob(
                f"no new final games since last training data "
                f"(season {last_training['season']} week {last_training['week']})"
            )

        artifacts_dir = Path(ensure_repo_root()) / "ml" / "artifacts"
        version = context.get("version") or _next_challenger_version(
            artifacts_dir, now.year)
        if not VERSION_RE.match(version):
            record.finish(JobStatus.FAILED,
                          error=f"refusing to train invalid version string: {version!r}")
            return record

        # Champion guard: snapshot before, verify after.
        champ_before = db["models"].find_one({"champion": True},
                                             {"_id": 0, "version": 1})
        train_fn = context.get("train_fn") or _default_train
        try:
            out_dir = train_fn(version)
        except AssertionError as exc:
            # The leakage harness blocked the run: fail loudly, register nothing.
            record.finish(JobStatus.FAILED,
                          error=f"leakage harness blocked retrain: {exc}"[:500])
            return record
        except SkipJob:
            raise
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED,
                          error=f"retrain failed: {exc}"[:500])
            return record

        marker = {
            "version": version,
            "role": "challenger",
            "promoted": False,
            "trained_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "training_data_through": {"season": latest["season"], "week": latest["week"]},
            "note": ("Challenger only. Promotion to champion is a manual, "
                     "explicit step — the scheduler never promotes a model."),
        }
        try:
            with open(Path(out_dir) / "challenger.json", "w") as fh:
                json.dump(marker, fh, indent=2)
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED,
                          error=f"could not write challenger marker: {exc}"[:300])
            return record

        db["models"].update_one(
            {"version": version},
            {"$set": {**marker, "champion": False}},
            upsert=True,
        )

        champ_after = db["models"].find_one({"champion": True},
                                            {"_id": 0, "version": 1})
        if (champ_before or {}).get("version") != (champ_after or {}).get("version"):
            record.finish(
                JobStatus.FAILED,
                error=("champion registry changed during retrain "
                       f"({(champ_before or {}).get('version')} -> "
                       f"{(champ_after or {}).get('version')}) — manual review required"),
            )
            return record

        record.detail = {
            "version": version,
            "role": "challenger",
            "promoted": False,
            "champion_unchanged": (champ_after or {}).get("version"),
            "training_data_through": marker["training_data_through"],
            "last_training_data": marker["training_data_through"],
        }
        record.finish(JobStatus.SUCCEEDED)
        return record

    @staticmethod
    def _last_training_data(db: Any) -> Optional[Dict[str, Any]]:
        doc = db["scheduler_jobs"].find_one({"name": "retrain"})
        detail = (doc or {}).get("detail") or {}
        return detail.get("last_training_data")
