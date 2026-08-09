# research_platform/scorecard.py
"""Human-readable factor-validation scorecard (pure summarization, no verdicts).

Rolls the platform's already-computed evidence — per-date IC, quantile backtests,
portfolio results, correlations, regime slices — into per-factor and per-portfolio
metric tables, a group backtest, and correlation matrices, plus a readable
``scorecard.md``. This layer deliberately renders NO pass/fail judgement: it only
computes and presents metrics so the researcher can judge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def win_rate(net_returns: pd.Series) -> float:
    """Fraction of periods with a strictly positive net return."""
    clean = pd.to_numeric(net_returns, errors="coerce").dropna()
    return float((clean > 0).mean()) if len(clean) else np.nan


def sortino_ratio(net_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized return over annualized downside deviation (NaN if no downside)."""
    clean = pd.to_numeric(net_returns, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    downside = clean.clip(upper=0.0)
    downside_dev = float(downside.std(ddof=1))
    if not downside_dev > 0:
        return np.nan
    annual_return = float(clean.mean() * periods_per_year)
    return annual_return / (downside_dev * np.sqrt(periods_per_year))


def calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """Annualized return divided by the magnitude of max drawdown (NaN if flat)."""
    if not max_drawdown or not np.isfinite(max_drawdown) or max_drawdown == 0:
        return np.nan
    return float(annualized_return) / abs(float(max_drawdown))


# append to research_platform/scorecard.py
from .correlation import factor_rank_turnover
from .evaluation import evaluate_ic, group_stratification_table


def _ic_stats(ic: pd.Series) -> dict:
    clean = pd.to_numeric(ic, errors="coerce").dropna()
    n = int(clean.count())
    mean = float(clean.mean()) if n else np.nan
    std = float(clean.std(ddof=1)) if n >= 2 else np.nan
    t = mean / (std / np.sqrt(n)) if std and std > 0 and n else np.nan
    icir = mean / std if std and std > 0 else np.nan
    hit = float((np.sign(clean) == np.sign(mean)).mean()) if n else np.nan
    return {"mean": mean, "std": std, "t": t, "n": n, "icir": icir, "hit": hit}


def build_factor_scorecard(
    factors: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    dates: pd.Index,
    n_groups: int = 5,
    min_names: int = 30,
    horizon: int = 5,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """One row per single factor: IC family, monotonicity, long-short, turnover."""
    strat = group_stratification_table(
        factors, forward_returns, n_groups=n_groups, min_names=min_names
    ).set_index("path")
    rows = []
    for name, panel in factors.items():
        spear = _ic_stats(evaluate_ic(panel, forward_returns, method="spearman", min_names=min_names))
        pear = _ic_stats(evaluate_ic(panel, forward_returns, method="pearson", min_names=min_names))
        direction = 1 if not np.isfinite(spear["mean"]) or spear["mean"] >= 0 else -1
        turnover = factor_rank_turnover(
            panel * direction, dates, rebalance_interval=horizon, min_names=min_names
        )
        coverage = float(panel.reindex(dates).notna().mean().mean())
        rows.append(
            {
                "factor": name,
                "rank_ic": spear["mean"],
                "rank_ic_t": spear["t"],
                "pearson_ic": pear["mean"],
                "icir": spear["icir"],
                "icir_annualized": spear["icir"] * np.sqrt(periods_per_year / horizon)
                if np.isfinite(spear["icir"])
                else np.nan,
                "ic_hit_rate": spear["hit"],
                "monotonicity": float(strat.loc[name, "monotonicity"]) if name in strat.index else np.nan,
                "long_short_mean": float(strat.loc[name, "top_bottom_mean"]) if name in strat.index else np.nan,
                "long_short_t": float(strat.loc[name, "top_bottom_t"]) if name in strat.index else np.nan,
                "rank_turnover": turnover,
                "coverage": coverage,
                "n_obs": spear["n"],
            }
        )
    return pd.DataFrame(rows)
