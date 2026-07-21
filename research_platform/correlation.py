"""In-sample factor redundancy and turnover diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CorrelationEstimate:
    values: pd.DataFrame
    verified: pd.DataFrame
    counts: pd.DataFrame


def factor_value_correlation(
    factors: dict[str, pd.DataFrame],
    dates: pd.Index,
    directions: dict[str, int],
    min_names: int = 30,
    min_dates: int = 60,
) -> CorrelationEstimate:
    """Median per-date cross-sectional Spearman correlations on IS dates."""
    names = sorted(factors)
    if not names:
        raise ValueError("at least one factor is required")
    missing_directions = set(names).difference(directions)
    if missing_directions:
        raise ValueError(f"missing factor directions: {sorted(missing_directions)}")
    observations = {(left, right): [] for left in names for right in names}

    for date in pd.Index(dates):
        cross_section = pd.DataFrame(
            {
                name: pd.to_numeric(factors[name].loc[date], errors="coerce")
                * int(directions[name])
                for name in names
            }
        ).replace([np.inf, -np.inf], np.nan)
        daily = cross_section.corr(method="spearman", min_periods=min_names)
        for left in names:
            for right in names:
                value = daily.loc[left, right]
                if np.isfinite(value):
                    observations[(left, right)].append(float(value))

    values = pd.DataFrame(np.nan, index=names, columns=names, dtype=float)
    counts = pd.DataFrame(0, index=names, columns=names, dtype=int)
    for pair, samples in observations.items():
        counts.loc[pair[0], pair[1]] = len(samples)
        if samples:
            values.loc[pair[0], pair[1]] = float(np.median(samples))
    verified = counts.ge(min_dates)
    return CorrelationEstimate(values=values, verified=verified, counts=counts)


def ic_correlation(
    ic_series: pd.DataFrame,
    directions: dict[str, int],
    shrinkage: float = 0.5,
) -> CorrelationEstimate:
    """Direction-align daily IC and apply fixed diagonal shrinkage."""
    if not 0 <= shrinkage <= 1:
        raise ValueError("shrinkage must be within [0, 1]")
    names = sorted(directions)
    aligned = ic_series.reindex(columns=names).apply(pd.to_numeric, errors="coerce")
    aligned = aligned.mul(pd.Series(directions, dtype=float), axis=1)
    valid = aligned.notna().astype(int)
    counts = valid.T.dot(valid).astype(int)
    raw = aligned.corr(min_periods=2).reindex(index=names, columns=names)
    values = raw * shrinkage
    np.fill_diagonal(values.values, 1.0)
    verified = counts.ge(2)
    np.fill_diagonal(verified.values, True)
    return CorrelationEstimate(values=values, verified=verified, counts=counts)


def connected_correlation_clusters(
    correlation: pd.DataFrame,
    verified: pd.DataFrame,
    threshold: float = 0.75,
) -> pd.DataFrame:
    """Build deterministic hard-correlation connected components."""
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be within [0, 1]")
    names = sorted(set(correlation.index).intersection(correlation.columns))
    values = correlation.reindex(index=names, columns=names)
    checks = verified.reindex(index=names, columns=names).fillna(False)
    adjacency = {name: set() for name in names}
    for position, left in enumerate(names):
        for right in names[position + 1 :]:
            value = values.loc[left, right]
            edge = not bool(checks.loc[left, right]) or (
                np.isfinite(value) and abs(float(value)) >= threshold
            )
            if edge:
                adjacency[left].add(right)
                adjacency[right].add(left)

    rows = []
    visited: set[str] = set()
    cluster_number = 0
    for root in names:
        if root in visited:
            continue
        stack = [root]
        members = []
        visited.add(root)
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        members = sorted(members)
        for factor in members:
            rows.append(
                {
                    "factor": factor,
                    "cluster": cluster_number,
                    "cluster_size": len(members),
                    "members": "|".join(members),
                }
            )
        cluster_number += 1
    return pd.DataFrame(rows).sort_values("factor").reset_index(drop=True)


def factor_rank_turnover(
    factor: pd.DataFrame,
    dates: pd.Index,
    rebalance_interval: int = 5,
    min_names: int = 30,
) -> float:
    """Mean absolute percentile-rank change at fixed IS rebalance dates."""
    if rebalance_interval < 1:
        raise ValueError("rebalance_interval must be positive")
    scheduled = factor.reindex(index=dates).iloc[::rebalance_interval]
    ranks = scheduled.rank(axis=1, pct=True, method="average")
    changes = []
    for position in range(1, len(ranks)):
        pair = pd.concat(
            {"previous": ranks.iloc[position - 1], "current": ranks.iloc[position]},
            axis=1,
        ).dropna()
        if len(pair) >= min_names:
            changes.append(float((pair["current"] - pair["previous"]).abs().mean()))
    return float(np.mean(changes)) if changes else np.nan
