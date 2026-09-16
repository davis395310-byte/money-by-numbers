"""Load normalized NFL game data for the ML layer.

Reads the nflverse schedules parquet through the Phase 2 provider's
on-disk cache (same bytes the ingestion pipeline uses — no second source
of truth) and returns a normalized, chronologically sortable frame.

Every row carries a timezone-aware ``kickoff`` (UTC). Rows are NOT
filtered by status here: callers decide whether they need finals only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

# Allow ``import ml.data_loader`` from the repo root as well as from inside
# the ``ml`` package directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.data_providers.nflverse import (  # noqa: E402
    SCHEDULES_URL,
    NflverseProvider,
    normalize_team_abbr,
)

#: Columns pulled from the schedules parquet.
_SCHEDULE_COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "gametime",
    "away_team", "away_score", "home_team", "home_score",
    "location", "overtime",
    "spread_line", "total_line",
    "away_rest", "home_rest",
    "div_game", "roof", "stadium",
]

#: Normalized output columns.
OUTPUT_COLUMNS = [
    "game_id", "season", "week", "game_type", "kickoff",
    "home_team", "away_team", "home_score", "away_score",
    "status", "overtime", "location", "div_game", "roof",
    "home_rest", "away_rest", "spread_line", "total_line",
]


def _parse_kickoff(gameday: object, gametime: object) -> Optional[pd.Timestamp]:
    """Parse nflverse gameday/gametime (Eastern) into a UTC timestamp."""
    if gameday is None or pd.isna(gameday):
        return None
    text = str(gameday).strip()[:10]
    gt = "" if gametime is None or pd.isna(gametime) else str(gametime).strip()
    if gt:
        text = f"{text} {gt}"
    # Reuse the provider's timezone logic: gametime is America/New_York.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        naive = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            naive = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return pd.Timestamp(naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC")))


def load_games(
    seasons: Iterable[int],
    game_types: tuple[str, ...] = ("REG",),
    provider: Optional[NflverseProvider] = None,
) -> pd.DataFrame:
    """Load normalized games for ``seasons`` from the nflverse cache.

    Args:
        seasons: Seasons to include (e.g. ``range(1999, 2026)``).
        game_types: Game types to keep (default regular season only).
        provider: Optional provider (a default one is constructed; its
            on-disk cache means repeat calls don't re-download).

    Returns:
        DataFrame with :data:`OUTPUT_COLUMNS`, one row per game, plus
        ``home_win`` (1/0/None), ``margin`` (home - away), ``total``.
        ``status`` is ``"final"`` when both scores are present, else
        ``"scheduled"``. Missing source values stay ``None``/``NaN``.
    """
    provider = provider or NflverseProvider()
    df = provider._read_parquet(SCHEDULES_URL, columns=_SCHEDULE_COLUMNS)
    df = df[df["season"].isin(set(seasons))].copy()
    if game_types:
        df = df[df["game_type"].isin(set(game_types))].copy()

    records = []
    for row in df.itertuples(index=False):
        home = normalize_team_abbr(row.home_team)
        away = normalize_team_abbr(row.away_team)
        hs = None if pd.isna(row.home_score) else int(row.home_score)
        aws = None if pd.isna(row.away_score) else int(row.away_score)
        kickoff = _parse_kickoff(row.gameday, row.gametime)
        records.append(
            {
                "game_id": str(row.game_id),
                "season": int(row.season),
                "week": int(row.week),
                "game_type": str(row.game_type),
                "kickoff": kickoff,
                "home_team": home,
                "away_team": away,
                "home_score": hs,
                "away_score": aws,
                "status": "final" if (hs is not None and aws is not None) else "scheduled",
                "overtime": None if pd.isna(row.overtime) else int(row.overtime),
                "location": None if pd.isna(row.location) else str(row.location),
                "div_game": None if pd.isna(row.div_game) else int(row.div_game),
                "roof": None if pd.isna(row.roof) else str(row.roof).lower(),
                "home_rest": None if pd.isna(row.home_rest) else int(row.home_rest),
                "away_rest": None if pd.isna(row.away_rest) else int(row.away_rest),
                "spread_line": None if pd.isna(row.spread_line) else float(row.spread_line),
                "total_line": None if pd.isna(row.total_line) else float(row.total_line),
            }
        )
    out = pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)
    out["home_win"] = (
        (out["home_score"] > out["away_score"]).astype("Int64").where(out["status"] == "final")
    )
    out["margin"] = (out["home_score"] - out["away_score"]).where(out["status"] == "final")
    out["total"] = (out["home_score"] + out["away_score"]).where(out["status"] == "final")
    out = out.sort_values("kickoff", kind="mergesort").reset_index(drop=True)
    return out
