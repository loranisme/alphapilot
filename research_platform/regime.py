"""Subperiod (regime) stability for factors and portfolios.

Every conclusion the platform produces is measured over a single OOS window, so a
signal that only works in one regime (e.g. the 2022-2024 AI-momentum stretch) can
look strong on the full sample while being fragile. This module slices the window
into subperiods and reports, per subperiod, the factor IC and the portfolio's net
performance, plus a sign-consistency summary — turning a single point estimate
into an explicit validity envelope.

Pure functions over already-computed daily IC series and net-return series; no data
loading and no leakage-relevant choices (slicing is on realized OOS dates only).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calendar_year_labels(dates: pd.DatetimeIndex) -> pd.Series:
    """Label each date with its calendar year (a natural regime proxy)."""
    index = pd.DatetimeIndex(dates)
    return pd.Series(index.year.astype(str), index=index, name="subperiod")


def subperiod_labels(
    dates: pd.DatetimeIndex, boundaries: list[str] | list[pd.Timestamp]
) -> pd.Series:
    """Label dates by explicit cut points, e.g. ``["2021-01-01", "2023-01-01"]``.

    Produces half-open ``[left, right)`` buckets named by their span, so callers
    can define regimes that do not align with calendar years.
    """
    index = pd.DatetimeIndex(dates)
    cuts = [pd.Timestamp(b) for b in boundaries]
    edges = [index.min()] + sorted(cuts) + [index.max() + pd.Timedelta(days=1)]
    labels = pd.Series(index=index, dtype=object, name="subperiod")
    for left, right in zip(edges[:-1], edges[1:]):
        mask = (index >= left) & (index < right)
        span = f"{left.date()}..{(right - pd.Timedelta(days=1)).date()}"
        labels.loc[index[mask]] = span
    return labels


def group_daily_ic(ic_series: pd.Series, labels: pd.Series) -> pd.Series:
    """Mean of a daily IC series within each subperiod (label order preserved)."""
    aligned = pd.to_numeric(ic_series, errors="coerce")
    frame = pd.DataFrame({"ic": aligned, "label": labels.reindex(aligned.index)})
    grouped = frame.dropna(subset=["label"]).groupby("label")["ic"].mean()
    ordered = pd.unique(labels.reindex(aligned.index).dropna())
    return grouped.reindex(ordered)


def ic_stability_summary(subperiod_ic: pd.Series) -> dict:
    """Summarize per-subperiod IC: dominant sign, consistency, worst subperiod."""
    values = pd.to_numeric(subperiod_ic, errors="coerce").dropna()
    if values.empty:
        return {
            "n_subperiods": 0,
            "dominant_sign": 0,
            "sign_consistency": 0.0,
            "min_abs_ic": np.nan,
            "worst_subperiod": None,
            "mean_ic": np.nan,
        }
    dominant = 1 if values.mean() >= 0 else -1
    consistency = float((np.sign(values) == dominant).mean())
    worst = values.multiply(dominant).idxmin()  # subperiod least favourable to the sign
    return {
        "n_subperiods": int(values.size),
        "dominant_sign": dominant,
        "sign_consistency": consistency,
        "min_abs_ic": float(values.abs().min()),
        "worst_subperiod": worst,
        "mean_ic": float(values.mean()),
    }


def subperiod_performance(
    net_returns: pd.Series,
    labels: pd.Series,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """Per-subperiod annualized return, volatility, Sharpe and observation count."""
    net = pd.to_numeric(net_returns, errors="coerce")
    frame = pd.DataFrame({"net": net, "label": labels.reindex(net.index)}).dropna(
        subset=["label"]
    )
    rows = []
    for label in pd.unique(frame["label"]):
        block = frame.loc[frame["label"] == label, "net"].dropna()
        ann_return = float(block.mean() * periods_per_year) if len(block) else np.nan
        ann_vol = (
            float(block.std(ddof=1) * np.sqrt(periods_per_year))
            if len(block) > 1
            else np.nan
        )
        rows.append(
            {
                "subperiod": label,
                "n_obs": int(len(block)),
                "annualized_return": ann_return,
                "annualized_volatility": ann_vol,
                "sharpe": ann_return / ann_vol
                if ann_vol and ann_vol > 0
                else np.nan,
            }
        )
    return pd.DataFrame(rows)
