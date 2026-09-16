"""Model versioning for MONEY BY NUMBERS.

Version format: ``MNB-NFL-YYYY.NN``

- ``MNB-NFL`` — fixed product/model-family prefix.
- ``YYYY``     — training year (UTC).
- ``NN``       — zero-padded sequence number of the training run within
                  that year (01, 02, ...).

Examples (format only — these versions do not exist): ``MNB-NFL-2026.01``.

No version has been issued in Phase 1. This module provides the format
validator, the metadata dataclass, and a constructor helper that stamps a
new version. Nothing here claims any training run, performance number, or
calibration result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: Regex for the ``MNB-NFL-YYYY.NN`` version format.
VERSION_PATTERN = re.compile(r"^MNB-NFL-\d{4}\.\d{2}$")

VERSION_PREFIX = "MNB-NFL"


@dataclass
class ModelVersionMetadata:
    """Metadata recorded for every trained model version.

    All performance-related fields default to ``None``: a version record must
    never carry invented metrics. They are filled in only by a real training
    run (see pipelines/jobs/retrain.py).
    """

    version: str
    """Version string in ``MNB-NFL-YYYY.NN`` format."""

    training_period: str = ""
    """Human-readable training window, e.g. "2021-2025 regular seasons"."""

    features: list[str] = field(default_factory=list)
    """Feature column names used by this version (from ml.features schema)."""

    training_date: str = ""
    """UTC ISO-8601 date the training run completed."""

    calibration: dict[str, float] = field(default_factory=dict)
    """Calibration metrics (e.g. brier_score, log_loss) — real runs only."""

    performance: dict[str, float] = field(default_factory=dict)
    """Holdout performance metrics — real runs only, never invented."""

    def __post_init__(self) -> None:
        if not VERSION_PATTERN.match(self.version):
            raise ValueError(
                f"Invalid model version {self.version!r}: "
                f"expected format {VERSION_PREFIX}-YYYY.NN (e.g. {VERSION_PREFIX}-2026.01)."
            )


def make_version(sequence: int, year: int | None = None) -> str:
    """Build the next version string for ``sequence`` in ``year``.

    Args:
        sequence: 1-based training-run sequence number within the year.
        year: Training year (UTC). Defaults to the current year.

    Returns:
        Version string like ``MNB-NFL-2026.01``.

    Note: generating a version string is not a training run. A version only
    becomes real when a retrain job (pipelines/jobs/retrain.py) completes and
    records full metadata.
    """
    if sequence < 1:
        raise ValueError("sequence must be >= 1")
    year = year if year is not None else datetime.now(timezone.utc).year
    return f"{VERSION_PREFIX}-{year}.{sequence:02d}"
