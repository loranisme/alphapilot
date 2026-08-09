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
