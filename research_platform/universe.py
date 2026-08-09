"""Universe axis: restrict a point-in-time member set to a size bucket.

Every result the platform has produced is conditioned on one universe (large-cap
S&P 500). This module makes "which universe" a first-class, swappable axis so the
same pipeline can be re-run on, e.g., the smaller half of the index and each
conclusion can carry an explicit universe label.

Size is measured cross-sectionally per date from a supplied size panel (dollar
ADV or market-cap proxy), ranked only among names that are already point-in-time
index members that date (compose with :mod:`research_platform.providers`). The
result is a boolean membership mask the runners apply exactly like the PIT mask.

Pure functions, deterministic tie-breaking; no data loading.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SizeBucket:
    """A size-based sub-universe definition.

    ``kind='top'``/``'bottom'`` take the ``n`` largest/smallest names per date;
    ``kind='quantile'`` takes names whose size-rank percentile falls in
    ``[low, high]`` (0 = smallest, 1 = largest).
    """

    name: str
    kind: str
    n: int | None = None
    low: float | None = None
    high: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"top", "bottom", "quantile"}:
            raise ValueError("kind must be 'top', 'bottom', or 'quantile'")
        if self.kind in {"top", "bottom"}:
            if self.n is None or self.n < 1:
                raise ValueError("top/bottom buckets require n >= 1")
        else:
            if self.low is None or self.high is None:
                raise ValueError("quantile buckets require low and high")
            if not 0.0 <= self.low < self.high <= 1.0:
                raise ValueError("quantile band must satisfy 0 <= low < high <= 1")


def _selected_names(size_row: pd.Series, bucket: SizeBucket) -> pd.Index:
    ranked = size_row.dropna().sort_values(ascending=False, kind="stable")
    if ranked.empty:
        return pd.Index([])
    if bucket.kind == "top":
        return ranked.index[: bucket.n]
    if bucket.kind == "bottom":
        return ranked.index[-bucket.n :]
    # quantile: percentile of size, 0 = smallest, 1 = largest.
    count = len(ranked)
    percentile = 1.0 - (np.arange(count) + 0.5) / count  # rank 0 (largest) -> ~1
    keep = (percentile >= bucket.low) & (percentile <= bucket.high)
    return ranked.index[keep]


def size_bucket_membership(
    size_panel: pd.DataFrame,
    bucket: SizeBucket,
    base_membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per-date boolean membership for a size bucket.

    ``size_panel`` is ``dates x names`` (larger = bigger). When
    ``base_membership`` is given, only names that are already members on a date
    are eligible for ranking, so the size cut composes with PIT membership.
    """
    dates = size_panel.index
    columns = size_panel.columns
    membership = pd.DataFrame(False, index=dates, columns=columns)
    if base_membership is not None:
        base = base_membership.reindex(index=dates, columns=columns, fill_value=False)
    else:
        base = pd.DataFrame(True, index=dates, columns=columns)
    for date in dates:
        eligible = size_panel.loc[date].where(base.loc[date].astype(bool))
        selected = _selected_names(eligible, bucket)
        if len(selected):
            membership.loc[date, selected] = True
    return membership


def apply_size_bucket(
    member_mask: pd.DataFrame,
    size_panel: pd.DataFrame,
    bucket: SizeBucket,
) -> pd.DataFrame:
    """Intersect an existing PIT member mask with a size-bucket membership."""
    bucketed = size_bucket_membership(size_panel, bucket, base_membership=member_mask)
    aligned = member_mask.reindex(
        index=bucketed.index, columns=bucketed.columns, fill_value=False
    )
    return aligned & bucketed


# Built-in size buckets, expressed on the available S&P 500 universe. These are
# honest size *cuts* of a large-cap index, not a true small-cap universe (that
# needs a separate constituent + price source), but they make the size axis
# runnable on existing data today.
BUILTIN_BUCKETS: dict[str, SizeBucket] = {
    "all": SizeBucket("all", "quantile", low=0.0, high=1.0),
    "mega_top100": SizeBucket("mega_top100", "top", n=100),
    "smaller_half": SizeBucket("smaller_half", "quantile", low=0.0, high=0.5),
    "larger_half": SizeBucket("larger_half", "quantile", low=0.5, high=1.0),
    "smallest150": SizeBucket("smallest150", "bottom", n=150),
}


def resolve_bucket(universe: str | SizeBucket) -> SizeBucket:
    """Resolve a bucket name or object to a :class:`SizeBucket`."""
    if isinstance(universe, SizeBucket):
        return universe
    if universe not in BUILTIN_BUCKETS:
        raise ValueError(
            f"unknown universe {universe!r}; choose from {sorted(BUILTIN_BUCKETS)}"
        )
    return BUILTIN_BUCKETS[universe]
