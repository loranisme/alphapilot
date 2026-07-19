"""Signal-to-weight conversion and cost-aware portfolio simulation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioResult:
    weights: pd.DataFrame
    gross_returns: pd.Series
    net_returns: pd.Series
    turnover: pd.Series
    metrics: dict[str, float]


def _group_weights(signal: pd.Series, quantile: float) -> pd.Series:
    clean = pd.to_numeric(signal, errors="coerce").dropna().sort_values()
    count = max(1, int(np.floor(len(clean) * quantile)))
    if len(clean) < 2 * count:
        return pd.Series(0.0, index=signal.index)
    weights = pd.Series(0.0, index=signal.index, dtype=float)
    weights.loc[clean.index[:count]] = -1.0 / count
    weights.loc[clean.index[-count:]] = 1.0 / count
    return weights


def build_long_short_weights(
    signal: pd.Series,
    quantile: float = 0.2,
    max_weight: float = 0.05,
    industry: pd.Series | None = None,
) -> pd.Series:
    if not 0 < quantile <= 0.5:
        raise ValueError("quantile must be within (0, 0.5]")
    if not 0 < max_weight <= 1:
        raise ValueError("max_weight must be within (0, 1]")
    if industry is None:
        weights = _group_weights(signal, quantile)
    else:
        frame = pd.concat({"signal": signal, "industry": industry}, axis=1).dropna()
        parts = []
        for _, group in frame.groupby("industry"):
            parts.append(_group_weights(group["signal"], quantile))
        weights = pd.concat(parts).groupby(level=0).sum().reindex(signal.index).fillna(0.0)
        long_total = weights.clip(lower=0).sum()
        short_total = -weights.clip(upper=0).sum()
        if long_total > 0:
            weights.loc[weights > 0] /= long_total
        if short_total > 0:
            weights.loc[weights < 0] /= short_total

    if weights.abs().max() > max_weight + 1e-12:
        raise ValueError("max_weight is too small for the selected portfolio")
    return weights


def simulate_portfolio(
    target_weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    cost_bps: float = 10.0,
) -> PortfolioResult:
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    targets = target_weights.reindex(
        index=asset_returns.index, columns=asset_returns.columns
    ).fillna(0.0)
    executed = targets.shift(1).fillna(0.0)
    returns = asset_returns.fillna(0.0)
    gross = (executed * returns).sum(axis=1)
    turnover = executed.diff().abs().sum(axis=1)
    turnover.iloc[0] = executed.iloc[0].abs().sum()
    net = gross - turnover * cost_bps / 10_000.0

    wealth = (1.0 + net).cumprod()
    drawdown = wealth.div(wealth.cummax()).sub(1.0)
    annualized_return = float(net.mean() * 252)
    annualized_volatility = float(net.std(ddof=1) * np.sqrt(252))
    metrics = {
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe": annualized_return / annualized_volatility
        if annualized_volatility > 0
        else np.nan,
        "max_drawdown": float(drawdown.min()),
        "average_turnover": float(turnover.mean()),
        "total_cost": float((turnover * cost_bps / 10_000.0).sum()),
    }
    return PortfolioResult(executed, gross, net, turnover, metrics)
