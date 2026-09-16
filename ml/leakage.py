"""Leakage-test harness for MONEY BY NUMBERS.

The mandatory invariant (product spec section 26):

    A historical prediction may only use information available
    strictly before kickoff.

``KickoffLeakageHarness`` enforces it three ways:

1. ``check_game`` — per-game audit: every input's ``as_of`` timestamp
   must be strictly before the game's kickoff.
2. ``check_feature_frame`` — structural audit of a built feature frame:
   the ``_data_through`` column (latest kickoff contributing to a row,
   see ``ml.features``) must be strictly before the row's kickoff.
3. ``check_truncation_invariance`` — behavioral audit: rebuilding the
   features on data truncated at a cutoff must produce *identical* rows
   for all games at or before the cutoff. Any difference proves a
   feature looked at the future.

``assert_no_leakage`` raises on any violation: a training or evaluation
run must call it before publishing metrics. A non-empty violation list
means the results are invalid and must not be recorded or displayed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import pandas as pd


@dataclass
class LeakageViolation:
    """A single detected leakage violation."""

    game_id: str
    feature: str
    feature_timestamp: datetime | None
    kickoff: datetime
    detail: str = ""


class LeakageHarness(ABC):
    """Abstract leakage-test harness."""

    @abstractmethod
    def check_game(
        self, game_id: str, kickoff: datetime, inputs: dict[str, Any]
    ) -> list[LeakageViolation]:
        """Check one game's prediction inputs for leakage.

        Args:
            game_id: Identifier of the game being predicted.
            kickoff: The game's kickoff time (timezone-aware).
            inputs: Mapping of feature name -> (value, as_of_timestamp).

        Returns:
            List of violations; empty means no leakage detected.
        """
        raise NotImplementedError

    @abstractmethod
    def check_backtest(self, games: list[dict[str, Any]]) -> list[LeakageViolation]:
        """Run the leakage check across a full backtest game set."""
        raise NotImplementedError


class KickoffLeakageHarness(LeakageHarness):
    """Concrete harness enforcing the strict pre-kickoff rule."""

    def check_game(
        self, game_id: str, kickoff: datetime, inputs: dict[str, Any]
    ) -> list[LeakageViolation]:
        violations: list[LeakageViolation] = []
        for name, payload in inputs.items():
            as_of = payload[1] if isinstance(payload, (tuple, list)) else payload
            if as_of is None:
                continue  # no contributing data: trivially safe
            as_of_dt = pd.Timestamp(as_of).to_pydatetime()
            kickoff_dt = pd.Timestamp(kickoff).to_pydatetime()
            if as_of_dt >= kickoff_dt:
                violations.append(
                    LeakageViolation(
                        game_id=game_id,
                        feature=name,
                        feature_timestamp=as_of_dt,
                        kickoff=kickoff_dt,
                        detail=f"input as_of {as_of_dt.isoformat()} is not before kickoff",
                    )
                )
        return violations

    def check_feature_frame(self, frame: pd.DataFrame) -> list[LeakageViolation]:
        """Audit a feature frame's ``_data_through`` column.

        Every row must satisfy ``_data_through < kickoff`` (or carry no
        contributing data). A single breach fails the whole frame.
        """
        violations: list[LeakageViolation] = []
        if "_data_through" not in frame.columns:
            return [
                LeakageViolation(
                    game_id="*",
                    feature="_data_through",
                    feature_timestamp=None,
                    kickoff=pd.Timestamp("1970-01-01", tz="UTC").to_pydatetime(),
                    detail="feature frame lacks the _data_through audit column",
                )
            ]
        for _idx, row in frame.iterrows():
            data_through = row["_data_through"]
            kickoff = row["kickoff"]
            if data_through is None or pd.isna(data_through):
                continue
            if pd.Timestamp(data_through) >= pd.Timestamp(kickoff):
                violations.append(
                    LeakageViolation(
                        game_id=str(row["game_id"]),
                        feature="_data_through",
                        feature_timestamp=pd.Timestamp(data_through).to_pydatetime(),
                        kickoff=pd.Timestamp(kickoff).to_pydatetime(),
                        detail="features used data from at or after kickoff",
                    )
                )
        return violations

    def check_truncation_invariance(
        self,
        build_fn: Callable[[pd.DataFrame], pd.DataFrame],
        games: pd.DataFrame,
        cutoff: pd.Timestamp,
        feature_cols: list[str] | None = None,
    ) -> list[LeakageViolation]:
        """Rebuild features on data truncated at ``cutoff``.

        Rows with ``kickoff <= cutoff`` must be byte-identical between the
        full build and the truncated build. Any difference proves a
        feature consumed post-cutoff information.
        """
        full = build_fn(games)
        truncated_games = games[pd.to_datetime(games["kickoff"]) <= cutoff].copy()
        truncated = build_fn(truncated_games)

        keep_ids = set(truncated["game_id"])
        full_sub = full[full["game_id"].isin(keep_ids)].copy()
        cols = feature_cols or [c for c in full.columns if not c.startswith("_")]
        cols = [c for c in cols if c in truncated.columns]

        merged = full_sub.set_index("game_id")[cols].sort_index()
        trunc = truncated.set_index("game_id")[cols].sort_index()
        diff = (merged.fillna(-99999.0).to_numpy() != trunc.fillna(-99999.0).to_numpy())

        violations: list[LeakageViolation] = []
        if diff.any():
            bad_rows, bad_cols = diff.nonzero()
            seen: set[str] = set()
            for r, c in zip(bad_rows, bad_cols):
                gid = str(merged.index[r])
                if gid in seen:
                    continue
                seen.add(gid)
                violations.append(
                    LeakageViolation(
                        game_id=gid,
                        feature=str(merged.columns[c]),
                        feature_timestamp=cutoff.to_pydatetime(),
                        kickoff=pd.Timestamp(
                            full_sub.set_index("game_id").loc[gid, "kickoff"]
                        ).to_pydatetime(),
                        detail="feature value changed when post-cutoff data was removed",
                    )
                )
        return violations

    def check_backtest(self, games: list[dict[str, Any]]) -> list[LeakageViolation]:
        """Audit a backtest plan: every test game's inputs must predate kickoff.

        ``games`` entries carry ``game_id``, ``kickoff``, and ``inputs``
        (feature -> (value, as_of)). Thin wrapper over :meth:`check_game`.
        """
        violations: list[LeakageViolation] = []
        for game in games:
            violations.extend(
                self.check_game(game["game_id"], game["kickoff"], game.get("inputs", {}))
            )
        return violations


def assert_no_leakage(violations: list[LeakageViolation]) -> None:
    """Raise if any leakage violations were detected.

    A training or evaluation run must call this before publishing metrics:
    a non-empty violation list means the results are invalid and must not
    be recorded or displayed.
    """
    if violations:
        summary = "; ".join(
            f"{v.game_id}:{v.feature} (as_of={v.feature_timestamp} "
            f">= kickoff={v.kickoff})"
            for v in violations[:10]
        )
        if len(violations) > 10:
            summary += f"; ... and {len(violations) - 10} more"
        raise AssertionError(f"Data leakage detected ({len(violations)} violations): {summary}")
