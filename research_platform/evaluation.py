"""Leakage-aware factor evaluation utilities."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def generate_purged_folds(
    dates: pd.Index,
    min_train: int,
    test_size: int,
    step: int,
    horizon: int,
    purge: int | None = None,
    embargo: int = 0,
    max_train: int | None = None,
) -> list[tuple[pd.Index, pd.Index]]:
    if min_train < 1 or test_size < 1 or step < 1 or horizon < 1:
        raise ValueError("fold sizes and horizon must be positive")
    purge = horizon if purge is None else purge
    if purge < horizon:
        raise ValueError("purge must be at least horizon")
    if embargo < 0:
        raise ValueError("embargo must be non-negative")
    ordered = pd.Index(dates).drop_duplicates().sort_values()
    folds: list[tuple[pd.Index, pd.Index]] = []
    test_start = min_train + purge
    while test_start + test_size <= len(ordered):
        train_end = test_start - purge
        train_start = max(0, train_end - max_train) if max_train else 0
        train = ordered[train_start:train_end]
        test = ordered[test_start : test_start + test_size]
        if len(train) >= min_train:
            folds.append((train, test))
        test_start += step + embargo
    return folds


def evaluate_ic(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    method: str = "spearman",
    min_names: int = 30,
) -> pd.Series:
    if method not in {"spearman", "pearson"}:
        raise ValueError("method must be 'spearman' or 'pearson'")
    values = {}
    for date in factor.index.intersection(forward_returns.index):
        pair = pd.concat(
            [factor.loc[date].rename("factor"), forward_returns.loc[date].rename("return")],
            axis=1,
        ).dropna()
        if (
            len(pair) < min_names
            or pair["factor"].nunique() <= 1
            or pair["return"].nunique() <= 1
        ):
            continue
        values[date] = pair["factor"].corr(pair["return"], method=method)
    return pd.Series(values, dtype=float).sort_index()


@dataclass(frozen=True)
class QuantileBacktestResult:
    group_returns: pd.DataFrame
    group_counts: pd.DataFrame
    spread: pd.Series


def run_quantile_backtest(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    n_groups: int = 5,
    min_names: int = 30,
) -> QuantileBacktestResult:
    if n_groups < 2:
        raise ValueError("n_groups must be at least 2")
    columns = [f"group_{group}" for group in range(1, n_groups + 1)]
    returns_by_date = {}
    counts_by_date = {}
    for date in factor.index.intersection(forward_returns.index):
        pair = pd.concat(
            [factor.loc[date].rename("factor"), forward_returns.loc[date].rename("return")],
            axis=1,
        ).dropna()
        if len(pair) < max(min_names, n_groups) or pair["factor"].nunique() <= 1:
            continue
        pair["group"] = pd.qcut(
            pair["factor"].rank(method="first"),
            q=n_groups,
            labels=range(1, n_groups + 1),
        )
        grouped = pair.groupby("group", observed=True)["return"]
        returns_by_date[date] = {f"group_{int(key)}": value for key, value in grouped.mean().items()}
        counts_by_date[date] = {f"group_{int(key)}": value for key, value in grouped.count().items()}
    group_returns = pd.DataFrame.from_dict(returns_by_date, orient="index").reindex(columns=columns)
    group_counts = pd.DataFrame.from_dict(counts_by_date, orient="index").reindex(columns=columns)
    spread = group_returns[columns[-1]] - group_returns[columns[0]] if not group_returns.empty else pd.Series(dtype=float)
    spread.name = "top_bottom"
    return QuantileBacktestResult(group_returns, group_counts, spread)


def group_stratification_table(
    scores: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    n_groups: int = 5,
    min_names: int = 30,
) -> pd.DataFrame:
    """Per-path quantile stratification of a composite score.

    For each path the cross-section is sorted into ``n_groups`` by score each
    day; the table reports the time-averaged mean forward return of every group,
    the monotonicity of the group->return relationship (Spearman of group order
    vs mean return, +1 = perfectly increasing / -1 = decreasing), and the
    top-minus-bottom spread with a naive t-stat. This is a descriptive
    robustness layer that sits between portfolio construction and the bootstrap
    significance test: it exposes signals that only work in one tail or sort
    non-monotonically, which a single IC or long-short Sharpe can hide.
    """
    group_cols = [f"group_{group}" for group in range(1, n_groups + 1)]
    order = pd.Series(range(1, n_groups + 1), index=group_cols, dtype=float)
    rows = []
    for path, score in scores.items():
        result = run_quantile_backtest(
            score, forward_returns, n_groups=n_groups, min_names=min_names
        )
        group_means = result.group_returns.reindex(columns=group_cols).mean()
        valid = group_means.dropna()
        monotonicity = (
            float(order.reindex(valid.index).corr(valid, method="spearman"))
            if len(valid) >= 2
            else np.nan
        )
        spread = pd.to_numeric(result.spread, errors="coerce").dropna()
        spread_mean = float(spread.mean()) if len(spread) else np.nan
        spread_std = float(spread.std(ddof=1)) if len(spread) >= 2 else np.nan
        spread_t = (
            spread_mean / (spread_std / np.sqrt(len(spread)))
            if spread_std and spread_std > 0
            else np.nan
        )
        row = {"path": path}
        row.update(
            {col: float(group_means[col]) if pd.notna(group_means[col]) else np.nan
             for col in group_cols}
        )
        row.update(
            {
                "monotonicity": monotonicity,
                "top_bottom_mean": spread_mean,
                "top_bottom_t": spread_t,
                "n_obs": int(result.group_returns.shape[0]),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
