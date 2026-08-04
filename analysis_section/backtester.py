"""
backtester.py
-------------
Walk-forward out-of-sample backtester for alpha factor composites.

Architecture
------------
* **Expanding-window** walk-forward: training set grows from a minimum window,
  test set advances in fixed steps.  Rolling window is also supported via
  `max_train_periods`.
* Each fold independently selects factors and computes IC-IR MVO weights on
  in-sample data, then evaluates the composite on the hold-out (OOS) set.
* Anti-overfitting measures applied:
    1. IC significance filter (p < `sig_level` on training IC)
    2. IC correlation shrinkage (λ·I + (1-λ)·C) to regularise MVO
    3. Per-factor weight cap (`max_factor_weight`)
    4. Minimum in-sample ICIR gate per factor (`min_is_ic_ir`)
    5. Ensemble-shrinkage: final OOS weights are averaged with the equal-weight
       fallback weighted by `ensemble_alpha`
* Public API:
    - `WalkForwardBacktester.run()` → `BacktestResult`
    - `BacktestResult.oos_ic_series`     – OOS IC per date per factor
    - `BacktestResult.oos_composite_ic`  – OOS composite IC time-series
    - `BacktestResult.fold_summaries`    – per-fold IS/OOS metrics
    - `BacktestResult.summary()` → dict  – headline statistics
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from research_platform.evaluation import generate_purged_folds

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    fold_id: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    is_ic_summary: pd.DataFrame          # factor -> {ic_mean, ic_std, ic_ir, p_value}
    oos_ic_series: pd.DataFrame          # date x factor IC
    weights: Dict[str, float]            # factor -> composite weight
    n_factors_selected: int
    oos_composite_ic: pd.Series          # daily composite IC in OOS window


@dataclass
class BacktestResult:
    fold_results: List[FoldResult] = field(default_factory=list)
    config: Dict = field(default_factory=dict)

    @property
    def oos_composite_ic(self) -> pd.Series:
        """Concatenated OOS composite IC across all folds, sorted by date."""
        parts = [f.oos_composite_ic for f in self.fold_results if not f.oos_composite_ic.empty]
        if not parts:
            return pd.Series(dtype=float)
        return pd.concat(parts).sort_index()

    @property
    def oos_ic_series(self) -> pd.DataFrame:
        """Concatenated OOS per-factor IC across all folds."""
        parts = [f.oos_ic_series for f in self.fold_results if not f.oos_ic_series.empty]
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts).sort_index()

    @property
    def fold_summaries(self) -> pd.DataFrame:
        rows = []
        for f in self.fold_results:
            oos_ic = f.oos_composite_ic.dropna()
            row = {
                "fold": f.fold_id,
                "train_start": f.train_start,
                "train_end": f.train_end,
                "test_start": f.test_start,
                "test_end": f.test_end,
                "n_factors": f.n_factors_selected,
                "oos_ic_mean": float(oos_ic.mean()) if len(oos_ic) else np.nan,
                "oos_ic_std": float(oos_ic.std()) if len(oos_ic) else np.nan,
                "oos_ic_ir": (
                    float(oos_ic.mean() / oos_ic.std())
                    if len(oos_ic) >= 2 and oos_ic.std() > 0
                    else np.nan
                ),
                "oos_n_periods": len(oos_ic),
            }
            rows.append(row)
        return pd.DataFrame(rows).set_index("fold")

    def summary(self, periods_per_year: float = 50.4) -> Dict:
        oos = self.oos_composite_ic.dropna()
        if len(oos) < 2:
            return {"error": "Insufficient OOS observations"}
        ic_mean = float(oos.mean())
        ic_std = float(oos.std())
        raw_icir = ic_mean / ic_std if ic_std > 0 else np.nan
        ann_icir = raw_icir * np.sqrt(periods_per_year) if raw_icir is not np.nan else np.nan
        t_stat, p_value = stats.ttest_1samp(oos.values, 0.0)
        return {
            "oos_ic_mean": round(ic_mean, 6),
            "oos_ic_std": round(ic_std, 6),
            "oos_raw_icir": round(raw_icir, 4),
            "oos_annualized_icir": round(ann_icir, 4),
            "oos_t_stat": round(float(t_stat), 4),
            "oos_p_value": float(p_value),
            "oos_n_periods": len(oos),
            "n_folds": len(self.fold_results),
        }


@dataclass
class StratifiedBacktestResult:
    """Quantile/group backtest result for one or many alpha factors."""

    group_return_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    spread_return_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    group_count_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: Dict = field(default_factory=dict)

    @property
    def group_mean_returns(self) -> pd.DataFrame:
        """Mean return of each factor quantile group."""
        if self.group_return_series.empty:
            return pd.DataFrame()
        return self.group_return_series.mean().unstack(level=1)


# ---------------------------------------------------------------------------
# IC utilities (self-contained, no dependency on factor_analysis.py)
# ---------------------------------------------------------------------------

def _future_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    return close.pct_change(horizon, fill_method=None).shift(-horizon)


def _sanitize_returns(ret: pd.DataFrame, max_abs: float = 0.30, q: float = 0.01) -> pd.DataFrame:
    cleaned = ret.mask(ret.abs() > max_abs)
    lo = cleaned.quantile(q, axis=1)
    hi = cleaned.quantile(1 - q, axis=1)
    return cleaned.clip(lower=lo, upper=hi, axis=0)


def _compute_ic_series(
    factor_panel: pd.DataFrame,        # date x ticker  (single factor)
    future_ret: pd.DataFrame,          # date x ticker
    method: str = "spearman",
    min_tickers: int = 30,
) -> pd.Series:
    """Cross-sectional IC for a single factor."""
    common_dates = factor_panel.index.intersection(future_ret.index)
    ic_vals = {}
    for dt in common_dates:
        fv = pd.to_numeric(factor_panel.loc[dt], errors="coerce")
        rv = pd.to_numeric(future_ret.loc[dt], errors="coerce")
        pair = pd.concat([fv.rename("f"), rv.rename("r")], axis=1).dropna()
        if len(pair) < min_tickers or pair["f"].nunique() <= 1 or pair["r"].nunique() <= 1:
            continue
        ic_vals[dt] = pair["f"].corr(pair["r"], method=method)
    return pd.Series(ic_vals)


def _compute_all_ic_series(
    multi_factor_table: pd.DataFrame,   # MultiIndex (ticker, factor) columns
    close: pd.DataFrame,
    horizon: int = 5,
    method: str = "spearman",
    min_tickers: int = 30,
    date_mask: Optional[pd.Index] = None,
) -> pd.DataFrame:
    """Compute IC for every factor, optionally restricted to `date_mask`."""
    future_ret = _sanitize_returns(_future_returns(close, horizon))
    factors = list(multi_factor_table.columns.get_level_values(1).unique())
    tickers = list(multi_factor_table.columns.get_level_values(0).unique())

    result = {}
    for factor in factors:
        panel = multi_factor_table.xs(factor, level=1, axis=1).reindex(columns=tickers)
        if date_mask is not None:
            panel = panel.loc[panel.index.intersection(date_mask)]
        ic = _compute_ic_series(panel, future_ret, method=method, min_tickers=min_tickers)
        if not ic.empty:
            result[factor] = ic

    return pd.DataFrame(result).sort_index()


def _quantile_group_labels(
    factor_values: pd.Series,
    n_groups: int,
) -> pd.Series:
    """Assign low-to-high factor values into integer groups 1..n_groups."""
    labels = pd.Series(np.nan, index=factor_values.index, dtype=float)
    clean = pd.to_numeric(factor_values, errors="coerce").dropna()
    if n_groups < 2:
        raise ValueError("n_groups must be at least 2.")
    if len(clean) < n_groups or clean.nunique() <= 1:
        return labels

    # Rank first so qcut remains stable when many names share similar values.
    ranked = clean.rank(method="first")
    grouped = pd.qcut(ranked, q=n_groups, labels=range(1, n_groups + 1))
    labels.loc[grouped.index] = grouped.astype(int).astype(float)
    return labels


def _compute_factor_group_returns(
    factor_panel: pd.DataFrame,
    future_ret: pd.DataFrame,
    n_groups: int = 5,
    min_tickers: int = 30,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Equal-weight forward returns for factor quantile groups.

    Group 1 contains the lowest factor values; group n contains the highest.
    """
    group_cols = [f"group_{i}" for i in range(1, n_groups + 1)]
    common_dates = factor_panel.index.intersection(future_ret.index)
    group_returns = pd.DataFrame(index=common_dates, columns=group_cols, dtype=float)
    group_counts = pd.DataFrame(index=common_dates, columns=group_cols, dtype=float)

    for dt in common_dates:
        fv = pd.to_numeric(factor_panel.loc[dt], errors="coerce")
        rv = pd.to_numeric(future_ret.loc[dt], errors="coerce")
        pair = pd.concat([fv.rename("factor"), rv.rename("ret")], axis=1).dropna()
        if len(pair) < max(min_tickers, n_groups) or pair["factor"].nunique() <= 1:
            continue

        pair["group"] = _quantile_group_labels(pair["factor"], n_groups=n_groups)
        pair = pair.dropna(subset=["group"])
        if pair["group"].nunique() < 2:
            continue

        grouped = pair.groupby("group")["ret"]
        means = grouped.mean()
        counts = grouped.count()
        for group_id in range(1, n_groups + 1):
            col = f"group_{group_id}"
            group_returns.loc[dt, col] = means.get(float(group_id), np.nan)
            group_counts.loc[dt, col] = counts.get(float(group_id), np.nan)

    return group_returns.dropna(how="all"), group_counts.dropna(how="all")


def _monotonicity_score(group_means: pd.Series) -> float:
    """Fraction of low-high group pairs whose returns are correctly ordered."""
    vals = pd.to_numeric(group_means, errors="coerce").dropna().values
    if len(vals) < 2:
        return np.nan

    ordered = 0
    total = 0
    for i in range(len(vals) - 1):
        for j in range(i + 1, len(vals)):
            total += 1
            if vals[j] >= vals[i]:
                ordered += 1
    return ordered / total if total else np.nan


def _summarize_factor_group_backtest(
    factor_name: str,
    group_returns: pd.DataFrame,
    periods_per_year: float,
) -> Dict:
    """Build headline diagnostics for one factor's group return table."""
    row = {"factor": factor_name}
    group_means = group_returns.mean()
    for col, value in group_means.items():
        row[f"{col}_mean"] = float(value) if pd.notna(value) else np.nan

    bottom_col = "group_1"
    top_col = group_returns.columns[-1]
    spread = (group_returns[top_col] - group_returns[bottom_col]).dropna()
    row["observations"] = int(spread.count())
    row["bottom_mean"] = float(group_returns[bottom_col].mean()) if bottom_col in group_returns else np.nan
    row["top_mean"] = float(group_returns[top_col].mean()) if top_col in group_returns else np.nan
    row["top_bottom_mean"] = float(spread.mean()) if len(spread) else np.nan
    row["top_bottom_std"] = float(spread.std(ddof=1)) if len(spread) >= 2 else np.nan
    row["top_bottom_ir"] = (
        row["top_bottom_mean"] / row["top_bottom_std"]
        if pd.notna(row["top_bottom_std"]) and row["top_bottom_std"] > 0
        else np.nan
    )
    row["top_bottom_annualized_return"] = (
        row["top_bottom_mean"] * periods_per_year
        if pd.notna(row["top_bottom_mean"])
        else np.nan
    )
    row["top_bottom_annualized_ir"] = (
        row["top_bottom_ir"] * np.sqrt(periods_per_year)
        if pd.notna(row["top_bottom_ir"])
        else np.nan
    )
    row["hit_ratio"] = float((spread > 0).mean()) if len(spread) else np.nan
    row["cumulative_top_bottom_return"] = (
        float((1.0 + spread).prod() - 1.0)
        if len(spread)
        else np.nan
    )

    if len(spread) >= 2 and spread.std(ddof=1) > 0:
        t_stat, p_value = stats.ttest_1samp(spread.values, 0.0)
        row["t_stat"] = float(t_stat)
        row["p_value"] = float(p_value)
    else:
        row["t_stat"] = np.nan
        row["p_value"] = np.nan

    row["monotonicity_score"] = _monotonicity_score(group_means)
    if group_means.notna().sum() >= 2 and group_means.nunique(dropna=True) > 1:
        corr, mono_p = stats.spearmanr(
            np.arange(1, len(group_means) + 1),
            group_means.values,
            nan_policy="omit",
        )
        row["group_spearman_corr"] = float(corr)
        row["group_spearman_p_value"] = float(mono_p)
    else:
        row["group_spearman_corr"] = np.nan
        row["group_spearman_p_value"] = np.nan

    return row


def run_stratified_backtest(
    multi_factor_table: pd.DataFrame,
    close: pd.DataFrame,
    horizon: int = 5,
    n_groups: int = 5,
    min_tickers: int = 30,
    max_abs_return: float = 0.30,
    winsorize_q: float = 0.01,
    periods_per_year: Optional[float] = None,
) -> StratifiedBacktestResult:
    """
    Run a cross-sectional quantile backtest for every alpha in a factor table.

    Each date is sorted by factor value into `n_groups`; the result compares
    the future equal-weight return of the highest factor group against the
    lowest factor group.
    """
    if not isinstance(multi_factor_table.columns, pd.MultiIndex):
        raise ValueError("multi_factor_table must have MultiIndex columns: (Ticker, FactorName).")
    if n_groups < 2:
        raise ValueError("n_groups must be at least 2.")

    if periods_per_year is None:
        periods_per_year = 252.0 / max(horizon, 1)

    future_ret = _sanitize_returns(
        _future_returns(close, horizon=horizon),
        max_abs=max_abs_return,
        q=winsorize_q,
    )
    factors = list(multi_factor_table.columns.get_level_values(1).unique())
    tickers = list(multi_factor_table.columns.get_level_values(0).unique())

    group_return_parts = {}
    group_count_parts = {}
    spread_parts = {}
    summary_rows = []

    for factor in factors:
        panel = multi_factor_table.xs(factor, level=1, axis=1).reindex(columns=tickers)
        group_returns, group_counts = _compute_factor_group_returns(
            panel,
            future_ret.reindex(columns=tickers),
            n_groups=n_groups,
            min_tickers=min_tickers,
        )
        if group_returns.empty:
            continue

        group_return_parts[factor] = group_returns
        group_count_parts[factor] = group_counts
        spread_parts[factor] = group_returns.iloc[:, -1] - group_returns.iloc[:, 0]
        summary_rows.append(
            _summarize_factor_group_backtest(
                factor_name=factor,
                group_returns=group_returns,
                periods_per_year=periods_per_year,
            )
        )

    group_return_series = (
        pd.concat(group_return_parts, axis=1).sort_index()
        if group_return_parts
        else pd.DataFrame()
    )
    group_count_series = (
        pd.concat(group_count_parts, axis=1).sort_index()
        if group_count_parts
        else pd.DataFrame()
    )
    spread_return_series = (
        pd.DataFrame(spread_parts).sort_index()
        if spread_parts
        else pd.DataFrame()
    )
    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary = summary.set_index("factor").sort_values("top_bottom_mean", ascending=False)

    return StratifiedBacktestResult(
        group_return_series=group_return_series,
        spread_return_series=spread_return_series,
        group_count_series=group_count_series,
        summary=summary,
        config={
            "horizon": horizon,
            "n_groups": n_groups,
            "min_tickers": min_tickers,
            "max_abs_return": max_abs_return,
            "winsorize_q": winsorize_q,
            "periods_per_year": periods_per_year,
        },
    )


def _ic_summary(ic_series: pd.DataFrame, ewm_halflife: Optional[int] = None) -> pd.DataFrame:
    """
    Summarise IC statistics per factor.

    Parameters
    ----------
    ic_series : DataFrame(date x factor)
    ewm_halflife : int or None
        If set, use exponentially weighted mean/std (EWM) so recent IC
        observations carry more weight.  Helps when the IS window spans
        multiple regimes (e.g. 2022 bear + 2023 bull).
        Typical value: 60–90 (periods).
    """
    s = pd.DataFrame(index=ic_series.columns)

    if ewm_halflife is not None and ewm_halflife > 0:
        ewm = ic_series.ewm(halflife=ewm_halflife, min_periods=10)
        # Take the last EWM value (most recent estimate)
        s["ic_mean"] = ewm.mean().iloc[-1]
        s["ic_std"] = ewm.std().iloc[-1].clip(lower=1e-8)
    else:
        s["ic_mean"] = ic_series.mean()
        s["ic_std"] = ic_series.std(ddof=1)

    s["ic_ir"] = s["ic_mean"] / s["ic_std"].replace(0, np.nan)
    s["n"] = ic_series.count()
    s["ic_se"] = ic_series.std(ddof=1) / np.sqrt(s["n"])    # t-test uses simple SE
    s["t_stat"] = ic_series.mean() / s["ic_se"]             # simple mean for t-test
    valid = s["n"] >= 2
    s["p_value"] = np.nan
    s.loc[valid, "p_value"] = (
        stats.t.sf(np.abs(s.loc[valid, "t_stat"]), df=s.loc[valid, "n"] - 1) * 2
    )
    return s


# ---------------------------------------------------------------------------
# Factor selection + weight building
# ---------------------------------------------------------------------------

def _select_factors(
    is_ic_summary: pd.DataFrame,
    top_n: int,
    sig_level: float,
    min_is_ic_ir: float,
    is_ic_series: Optional[pd.DataFrame] = None,
    recent_consistency: bool = True,
    factor_family_map: Optional[Dict[str, str]] = None,
) -> pd.Index:
    """
    Return names of selected factors that pass quality gates.

    Gates applied in order:
      1. |IS ICIR| >= min_is_ic_ir
      2. IS IC p-value < sig_level
      3. [optional] Recent-half IC has same sign as full-IS IC (stability filter)
         Prevents regime-specific factors from dominating the composite OOS.
      4. [optional] Family-minimum backfill: if `factor_family_map` is given,
         any regime family (reversal/momentum/value) with at least one
         gate-1-eligible factor keeps >=1 factor in the final selection, even
         if weaker than other families' factors on IS ICIR alone. Prevents
         the composite from concentrating entirely in whichever family
         happens to dominate the current IS window's regime.

    Falls back to looser criteria if strict gates eliminate all candidates.
    """
    eligible = is_ic_summary.copy()
    eligible["abs_ic_ir"] = eligible["ic_ir"].abs()

    # gate 1: minimum |ICIR|
    eligible = eligible[eligible["abs_ic_ir"] >= min_is_ic_ir]
    # gate 2: p-value significance
    sig_eligible = eligible[eligible["p_value"] < sig_level]

    if sig_eligible.empty:
        # fallback: drop significance gate
        sig_eligible = eligible

    if sig_eligible.empty:
        return pd.Index([])

    # gate 3: stability filter — IC direction must hold in recent IS half
    if recent_consistency and is_ic_series is not None and not is_ic_series.empty:
        n = len(is_ic_series)
        if n >= 40:
            recent_ic = is_ic_series.iloc[n // 2:]
            recent_mean = recent_ic.mean()
            full_sign = is_ic_summary["ic_mean"].apply(np.sign)
            recent_sign = recent_mean.apply(np.sign)
            stable_mask = full_sign.reindex(sig_eligible.index) == recent_sign.reindex(sig_eligible.index)
            stable = sig_eligible[stable_mask.fillna(False)]
            if not stable.empty:
                sig_eligible = stable
            # else: keep all candidates (no stable ones, accept the risk)

    selected = sig_eligible.nlargest(top_n, "abs_ic_ir").index

    # gate 4: family-minimum backfill
    if factor_family_map:
        selected_set = set(selected)
        represented = {factor_family_map[f] for f in selected_set if f in factor_family_map}
        for family in set(factor_family_map.values()):
            if family in represented:
                continue
            family_candidates = eligible[
                eligible.index.to_series().map(factor_family_map).eq(family)
            ]
            if family_candidates.empty:
                continue  # no gate-1-eligible factor from this family this fold
            best = family_candidates["abs_ic_ir"].idxmax()
            selected_set.add(best)
        selected = pd.Index(sorted(selected_set))

    return selected


def _build_weights(
    is_ic_summary: pd.DataFrame,
    selected: pd.Index,
    is_ic_series: pd.DataFrame,
    shrinkage: float,
    max_factor_weight: float,
    ensemble_alpha: float,
) -> Dict[str, float]:
    """
    IC-IR MVO weights with correlation shrinkage, sign-adjusted, capped.

    w ∝ C^{-1} × ic_ir_vec   where C = shrunk IC correlation matrix
    """
    if len(selected) == 0:
        return {}

    sub_summary = is_ic_summary.loc[selected]
    ic_ir = (sub_summary["ic_mean"] / sub_summary["ic_std"].replace(0, np.nan)).fillna(0.0)

    # Correlation-based MVO (more stable than full covariance)
    available = [f for f in selected if f in is_ic_series.columns]
    if len(available) >= 2:
        ic_sub = is_ic_series[available].dropna(how="all")
        corr_arr = ic_sub.corr().fillna(0.0).values.copy()
        np.fill_diagonal(corr_arr, 1.0)
        shrunk = (1 - shrinkage) * corr_arr + shrinkage * np.eye(len(corr_arr))
        try:
            corr_inv = np.linalg.inv(shrunk)
        except np.linalg.LinAlgError:
            corr_inv = np.eye(len(corr_arr))
        ic_ir_vec = ic_ir.reindex(available).fillna(0.0).values
        mvo_w = corr_inv @ ic_ir_vec
        raw_w = pd.Series(mvo_w, index=available)
        for f in set(selected) - set(available):
            raw_w[f] = ic_ir.get(f, 0.0)
        raw_w = raw_w.reindex(selected).fillna(0.0)
    else:
        raw_w = ic_ir.reindex(selected).fillna(0.0)

    # ensemble shrinkage toward equal weight
    n = len(selected)
    equal_w = pd.Series(1.0 / n, index=selected)
    blended = ensemble_alpha * raw_w + (1 - ensemble_alpha) * equal_w

    # cap individual weights: iterative clip-renormalize until convergence
    normalized = blended.copy()
    for _ in range(20):
        total = normalized.abs().sum()
        if total < 1e-12:
            return {f: 1.0 / n for f in selected}
        normalized = normalized / total
        clipped = normalized.clip(lower=-max_factor_weight, upper=max_factor_weight)
        if (clipped - normalized).abs().max() < 1e-10:
            normalized = clipped
            break
        normalized = clipped

    total = normalized.abs().sum()
    if total < 1e-12:
        return {f: 1.0 / n for f in selected}
    normalized = normalized / total
    return normalized.to_dict()


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

class WalkForwardBacktester:
    """
    Walk-forward IC backtester with expanding or rolling training window.

    Parameters
    ----------
    multi_factor_table : DataFrame with MultiIndex(ticker, factor) columns
        Factor values panel.
    close : DataFrame(date x ticker)
        Closing prices.
    horizon : int
        Prediction horizon in trading days (default 5).
    min_train_periods : int
        Minimum number of IC dates required to start a fold.
    test_periods : int
        Number of IC dates in each OOS test window.
    step_periods : int
        Advance step between folds.  Set equal to `test_periods` for
        non-overlapping folds.
    max_train_periods : int or None
        If set, caps training window (rolling window mode).
    top_n : int
        Maximum factors per fold.
    sig_level : float
        Two-sided t-test significance threshold for IS factor selection.
    min_is_ic_ir : float
        Minimum |raw ICIR| on IS data for a factor to be eligible.
    shrinkage : float
        IC correlation shrinkage parameter (0 = no shrinkage, 1 = identity).
    max_factor_weight : float
        Maximum absolute weight per factor after normalization.
    ensemble_alpha : float
        Blend between MVO weights (1.0) and equal weight (0.0).
    method : str
        IC correlation method: 'spearman' or 'pearson'.
    min_tickers : int
        Minimum cross-sectional observations per IC date.
    factor_family_map : dict or None
        Optional factor -> regime-family name mapping (e.g. FactorDesigner.
        FACTOR_FAMILY merged with FundamentalFactorDesigner.FACTOR_FAMILY).
        When set, every family with at least one IS-eligible factor keeps
        >=1 factor in each fold's selection (see `_select_factors` gate 4).
    """

    def __init__(
        self,
        multi_factor_table: pd.DataFrame,
        close: pd.DataFrame,
        horizon: int = 5,
        min_train_periods: int = 150,
        test_periods: int = 50,
        step_periods: int = 50,
        max_train_periods: Optional[int] = None,
        top_n: int = 15,
        sig_level: float = 0.10,
        min_is_ic_ir: float = 0.08,
        shrinkage: float = 0.15,
        max_factor_weight: float = 0.25,
        ensemble_alpha: float = 0.80,
        method: str = "spearman",
        min_tickers: int = 30,
        ewm_halflife: Optional[int] = None,
        factor_family_map: Optional[Dict[str, str]] = None,
        purge_periods: Optional[int] = None,
        embargo_periods: int = 0,
    ):
        self.multi_factor_table = multi_factor_table
        self.close = close
        self.horizon = horizon
        self.min_train_periods = min_train_periods
        self.test_periods = test_periods
        self.step_periods = step_periods
        self.max_train_periods = max_train_periods
        self.top_n = top_n
        self.sig_level = sig_level
        self.min_is_ic_ir = min_is_ic_ir
        self.shrinkage = shrinkage
        self.max_factor_weight = max_factor_weight
        self.ensemble_alpha = ensemble_alpha
        self.method = method
        self.min_tickers = min_tickers
        self.ewm_halflife = ewm_halflife
        self.factor_family_map = factor_family_map
        self.purge_periods = horizon if purge_periods is None else purge_periods
        self.embargo_periods = embargo_periods
        if self.purge_periods < self.horizon:
            raise ValueError("purge_periods must be at least horizon")

    # ------------------------------------------------------------------
    # Fold generation
    # ------------------------------------------------------------------

    def _ic_dates(self, ic_series: pd.DataFrame) -> pd.Index:
        return ic_series.dropna(how="all").index.sort_values()

    def _generate_folds(
        self, ic_dates: pd.Index
    ) -> List[Tuple[pd.Index, pd.Index]]:
        """Return list of (train_dates, test_dates) index pairs."""
        return generate_purged_folds(
            ic_dates,
            min_train=self.min_train_periods,
            test_size=self.test_periods,
            step=self.step_periods,
            horizon=self.horizon,
            purge=self.purge_periods,
            embargo=self.embargo_periods,
            max_train=self.max_train_periods,
        )

    # ------------------------------------------------------------------
    # Single-fold evaluation
    # ------------------------------------------------------------------

    def _run_fold(
        self,
        fold_id: int,
        train_dates: pd.Index,
        test_dates: pd.Index,
        full_ic_series: pd.DataFrame,
    ) -> FoldResult:
        # IS IC
        is_ic = full_ic_series.loc[full_ic_series.index.isin(train_dates)].dropna(how="all")
        is_summary = _ic_summary(is_ic, ewm_halflife=self.ewm_halflife)

        # Factor selection + weighting from IS only (with stability filter)
        selected = _select_factors(
            is_summary, self.top_n, self.sig_level, self.min_is_ic_ir,
            is_ic_series=is_ic, recent_consistency=True,
            factor_family_map=self.factor_family_map,
        )
        weights = _build_weights(
            is_summary, selected, is_ic,
            self.shrinkage, self.max_factor_weight, self.ensemble_alpha,
        )

        # OOS IC — reuse precomputed series restricted to test dates
        oos_ic = full_ic_series.loc[full_ic_series.index.isin(test_dates)].dropna(how="all")

        # Composite OOS IC = weighted sum of individual OOS IC streams
        if weights:
            w_series = pd.Series(weights)
            available_factors = [f for f in w_series.index if f in oos_ic.columns]
            if available_factors:
                w_sub = w_series.reindex(available_factors).fillna(0.0)
                w_sub = w_sub / w_sub.abs().sum()
                comp_ic = oos_ic[available_factors].fillna(0.0) @ w_sub
            else:
                comp_ic = pd.Series(dtype=float)
        else:
            comp_ic = pd.Series(dtype=float)

        return FoldResult(
            fold_id=fold_id,
            train_start=train_dates[0],
            train_end=train_dates[-1],
            test_start=test_dates[0],
            test_end=test_dates[-1],
            is_ic_summary=is_summary,
            oos_ic_series=oos_ic,
            weights=weights,
            n_factors_selected=len(weights),
            oos_composite_ic=comp_ic,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> BacktestResult:
        """
        Execute the full walk-forward backtest.

        Returns
        -------
        BacktestResult
        """
        log.info("Computing full IC series across all factors and dates ...")
        full_ic = _compute_all_ic_series(
            self.multi_factor_table,
            self.close,
            horizon=self.horizon,
            method=self.method,
            min_tickers=self.min_tickers,
        )

        ic_dates = self._ic_dates(full_ic)
        folds = self._generate_folds(ic_dates)
        log.info("Walk-forward: %d folds, %d total IC dates", len(folds), len(ic_dates))

        if not folds:
            warnings.warn(
                f"No folds generated. min_train_periods={self.min_train_periods}, "
                f"test_periods={self.test_periods}, total IC dates={len(ic_dates)}. "
                "Try reducing min_train_periods or test_periods."
            )
            return BacktestResult(config=self._config_dict())

        fold_results = []
        for fold_id, (train_dates, test_dates) in enumerate(folds):
            log.info(
                "Fold %d: train [%s, %s] → test [%s, %s]",
                fold_id,
                train_dates[0].date(), train_dates[-1].date(),
                test_dates[0].date(), test_dates[-1].date(),
            )
            result = self._run_fold(fold_id, train_dates, test_dates, full_ic)
            fold_results.append(result)

        return BacktestResult(fold_results=fold_results, config=self._config_dict())

    def _config_dict(self) -> Dict:
        return {
            "horizon": self.horizon,
            "min_train_periods": self.min_train_periods,
            "test_periods": self.test_periods,
            "step_periods": self.step_periods,
            "max_train_periods": self.max_train_periods,
            "top_n": self.top_n,
            "sig_level": self.sig_level,
            "min_is_ic_ir": self.min_is_ic_ir,
            "shrinkage": self.shrinkage,
            "max_factor_weight": self.max_factor_weight,
            "ensemble_alpha": self.ensemble_alpha,
            "method": self.method,
            "purge_periods": self.purge_periods,
            "embargo_periods": self.embargo_periods,
        }


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def _build_combined_factor_table(
    ticker_data: Dict[str, pd.DataFrame],
    include_fundamentals: bool = True,
    fundamental_cache_dir=None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, str]]:
    """
    Build the technical (Family A/B) + optional fundamental (Family C)
    factor table, the close-price matrix, and the combined factor->family
    map used for the walk-forward selector's family-minimum constraint.
    """
    try:
        from factor_design import FactorDesigner, FundamentalFactorDesigner
    except ImportError:
        from factor_section.factor_design import FactorDesigner, FundamentalFactorDesigner

    try:
        from factor_analysis import FactorAnalyzer
    except ImportError:
        from factor_section.factor_analysis import FactorAnalyzer

    designer = FactorDesigner()
    analyzer = FactorAnalyzer()

    log.info("Building technical factor table (%d tickers)...", len(ticker_data))
    multi_factor_table = designer.build_multi_ticker_factor_table(ticker_data)
    close = analyzer.build_close_matrix(ticker_data)
    factor_family_map = dict(FactorDesigner.FACTOR_FAMILY)

    if include_fundamentals:
        try:
            try:
                from fundamental_loader import load_fundamental_panel
            except ImportError:
                from factor_section.fundamental_loader import load_fundamental_panel

            log.info("Loading fundamental panel (%d tickers)...", len(ticker_data))
            fundamental = load_fundamental_panel(
                list(ticker_data.keys()), close.index, cache_dir=fundamental_cache_dir,
            )
            fund_designer = FundamentalFactorDesigner()
            fund_table = fund_designer.build_multi_ticker_factor_table(fundamental, close)
            if not fund_table.empty:
                multi_factor_table = pd.concat([multi_factor_table, fund_table], axis=1)
                factor_family_map.update(FundamentalFactorDesigner.FACTOR_FAMILY)
            else:
                log.warning("Fundamental factor table empty; proceeding with technical factors only.")
        except Exception as exc:
            log.warning("Skipping fundamental factors (%s)", exc)

    return multi_factor_table, close, factor_family_map


def run_walkforward_backtest(
    ticker_data: Dict[str, pd.DataFrame],
    horizon: int = 5,
    min_train_periods: int = 150,
    test_periods: int = 50,
    step_periods: int = 50,
    max_train_periods: Optional[int] = None,
    top_n: int = 15,
    sig_level: float = 0.10,
    min_is_ic_ir: float = 0.08,
    shrinkage: float = 0.15,
    max_factor_weight: float = 0.25,
    ensemble_alpha: float = 0.80,
    method: str = "spearman",
    min_tickers: int = 30,
    ewm_halflife: Optional[int] = None,
    include_fundamentals: bool = True,
    fundamental_cache_dir=None,
    verbose: bool = True,
) -> BacktestResult:
    """
    End-to-end helper: takes raw OHLCV dict, builds the combined technical +
    fundamental factor table, runs walk-forward backtest, prints summary.

    Parameters
    ----------
    ticker_data : dict  ticker -> DataFrame(date, OHLCV)
    include_fundamentals : bool
        Merge Family C (value/quality) factors from FundamentalFactorDesigner.
    See WalkForwardBacktester for other parameters.

    Returns
    -------
    BacktestResult
    """
    multi_factor_table, close, factor_family_map = _build_combined_factor_table(
        ticker_data, include_fundamentals=include_fundamentals,
        fundamental_cache_dir=fundamental_cache_dir,
    )

    backtester = WalkForwardBacktester(
        multi_factor_table=multi_factor_table,
        close=close,
        horizon=horizon,
        min_train_periods=min_train_periods,
        test_periods=test_periods,
        step_periods=step_periods,
        max_train_periods=max_train_periods,
        top_n=top_n,
        sig_level=sig_level,
        min_is_ic_ir=min_is_ic_ir,
        shrinkage=shrinkage,
        max_factor_weight=max_factor_weight,
        ensemble_alpha=ensemble_alpha,
        method=method,
        min_tickers=min_tickers,
        ewm_halflife=ewm_halflife,
        factor_family_map=factor_family_map,
    )

    result = backtester.run()

    if verbose:
        print("\n" + "=" * 60)
        print("Walk-Forward Backtest Summary")
        print("=" * 60)
        periods_per_year = 252.0 / horizon
        summ = result.summary(periods_per_year=periods_per_year)
        for k, v in summ.items():
            print(f"  {k:<30s}: {v}")
        print()
        print("Per-fold breakdown:")
        fs = result.fold_summaries
        print(fs[["oos_ic_mean", "oos_ic_std", "oos_ic_ir", "n_factors", "oos_n_periods"]].to_string())
        print()
        print("Factor selection per fold:")
        for fold in result.fold_results:
            selected = sorted(fold.weights.keys())
            signs = {f: "+" if fold.weights[f] > 0 else "-" for f in selected}
            factor_str = "  ".join(f"{signs[f]}{f}({fold.weights[f]:+.2f})" for f in selected)
            print(f"  Fold {fold.fold_id}: [{factor_str}]")
        print("=" * 60)

    return result


def run_alpha_stratified_backtest(
    ticker_data: Dict[str, pd.DataFrame],
    horizon: int = 5,
    n_groups: int = 5,
    min_tickers: int = 30,
    max_abs_return: float = 0.30,
    winsorize_q: float = 0.01,
    include_fundamentals: bool = True,
    fundamental_cache_dir=None,
    verbose: bool = True,
) -> StratifiedBacktestResult:
    """
    End-to-end helper: build the combined technical + fundamental factor
    table and run the quantile backtest.
    """
    multi_factor_table, close, _ = _build_combined_factor_table(
        ticker_data, include_fundamentals=include_fundamentals,
        fundamental_cache_dir=fundamental_cache_dir,
    )

    result = run_stratified_backtest(
        multi_factor_table=multi_factor_table,
        close=close,
        horizon=horizon,
        n_groups=n_groups,
        min_tickers=min_tickers,
        max_abs_return=max_abs_return,
        winsorize_q=winsorize_q,
    )

    if verbose:
        print("\n" + "=" * 60)
        print("Stratified Alpha Backtest Summary")
        print("=" * 60)
        if result.summary.empty:
            print("No valid factor groups generated.")
        else:
            display_cols = [
                "top_bottom_mean",
                "top_bottom_annualized_return",
                "top_bottom_ir",
                "top_bottom_annualized_ir",
                "hit_ratio",
                "monotonicity_score",
                "t_stat",
                "p_value",
                "observations",
            ]
            print(result.summary[display_cols].head(15).to_string())
        print("=" * 60)

    return result


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure project root is on sys.path
    _project_root = Path(__file__).resolve().parents[1]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    if str(_project_root / "factor_section") not in sys.path:
        sys.path.insert(0, str(_project_root / "factor_section"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / "data" / "cleaned"

    csv_files = sorted(data_root.glob("*_cleaned.csv"))
    if not csv_files:
        print(f"No cleaned CSV files found under {data_root}. Aborting.")
        sys.exit(1)

    print(f"Loading {len(csv_files)} tickers ...")
    ticker_data = {}
    for f in csv_files:
        ticker = f.name.replace("_cleaned.csv", "")
        # NOTE: do NOT use parse_dates=[0] — mixed DST offsets (-04:00/-05:00)
        # in the saved index make pandas' dtype inference silently blank rows.
        df = pd.read_csv(f, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce").tz_convert("America/New_York")
        ticker_data[ticker] = df

    # ── Regime-universal config (8-year data, 3-family design) ───────────────
    # 2018-2026 data covers 2018-19 chop, 2020 COVID crash+recovery, 2021
    # bull, 2022 bear, 2023-25 AI bull — multiple complete regimes instead of
    # the single AI-bull-dominated 2022-2025 window. min_is_ic_ir relaxed
    # 0.15→0.08 since the longer IS window gives more reliable IC estimates;
    # the family-minimum constraint (factor_family_map) prevents this from
    # re-concentrating into whichever family dominates a given fold's regime.
    # min_train_periods raised 150→300 so even fold 0 has a robust IS window.
    result = run_walkforward_backtest(
        ticker_data=ticker_data,
        horizon=5,
        min_train_periods=300,
        test_periods=50,
        step_periods=50,
        max_train_periods=None,  # expanding window
        top_n=15,
        sig_level=0.10,
        min_is_ic_ir=0.08,
        shrinkage=0.15,
        max_factor_weight=0.25,
        ensemble_alpha=0.80,
        ewm_halflife=None,
        include_fundamentals=True,
        verbose=True,
    )

    # Save OOS composite IC
    out_dir = project_root / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    oos_ic = result.oos_composite_ic
    oos_ic.to_csv(out_dir / "walkforward_oos_composite_ic.csv", header=True)
    result.fold_summaries.to_csv(out_dir / "walkforward_fold_summaries.csv")

    # ── Stratified/group backtest section for every current alpha ────────────
    stratified = run_alpha_stratified_backtest(
        ticker_data=ticker_data,
        horizon=5,
        n_groups=5,
        min_tickers=30,
        max_abs_return=0.30,
        winsorize_q=0.01,
        include_fundamentals=True,
        verbose=True,
    )
    stratified.summary.to_csv(out_dir / "stratified_alpha_summary.csv")
    stratified.group_mean_returns.to_csv(out_dir / "stratified_alpha_group_mean_returns.csv")
    stratified.spread_return_series.to_csv(out_dir / "stratified_alpha_top_bottom_returns.csv")
    print(f"\nBacktest reports saved to {out_dir}")
