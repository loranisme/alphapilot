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


@dataclass(frozen=True)
class BufferedTargetResult:
    targets: pd.DataFrame
    diagnostics: pd.DataFrame


def _equal_gross_weights(names: set, gross: float, cap: float) -> pd.Series:
    if not names or len(names) * cap < gross - 1e-12:
        raise ValueError("name cap is infeasible for selected cross-section")
    return pd.Series(gross / len(names), index=sorted(names), dtype=float)


def buffered_cross_section(
    score: pd.Series,
    previous: pd.Series,
    entry_quantile: float = 0.20,
    exit_quantile: float = 0.30,
    max_weight: float = 0.02,
) -> pd.Series:
    """Build a dollar-neutral target with entry and exit rank buffers."""
    if not 0 < entry_quantile <= exit_quantile <= 0.5:
        raise ValueError("quantiles must satisfy 0 < entry <= exit <= 0.5")
    if not 0 < max_weight <= 1:
        raise ValueError("max_weight must be within (0, 1]")
    numeric = pd.to_numeric(score, errors="coerce")
    valid = numeric.dropna().sort_values()
    if valid.nunique() <= 1:
        raise ValueError("signal cross-section is invalid")
    entry_n = max(1, int(np.floor(len(valid) * entry_quantile)))
    exit_n = max(entry_n, int(np.floor(len(valid) * exit_quantile)))
    prior = previous.reindex(score.index).fillna(0.0)
    prior_long = set(prior.index[prior > 0])
    prior_short = set(prior.index[prior < 0])
    long_names = set(valid.nlargest(entry_n).index) | (
        prior_long & set(valid.nlargest(exit_n).index)
    )
    short_names = set(valid.nsmallest(entry_n).index) | (
        prior_short & set(valid.nsmallest(exit_n).index)
    )
    target = pd.Series(0.0, index=score.index, dtype=float)
    long_weights = _equal_gross_weights(long_names, gross=1.0, cap=max_weight)
    short_weights = _equal_gross_weights(short_names, gross=1.0, cap=max_weight)
    target.loc[long_weights.index] = long_weights
    target.loc[short_weights.index] = -short_weights
    return target


def build_buffered_targets(
    scores: pd.DataFrame,
    rebalance_interval: int = 5,
    entry_quantile: float = 0.20,
    exit_quantile: float = 0.30,
    max_weight: float = 0.02,
) -> BufferedTargetResult:
    """Create stateful targets and hold through scheduled or invalid dates."""
    if rebalance_interval < 1:
        raise ValueError("rebalance_interval must be positive")
    targets = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    previous = pd.Series(0.0, index=scores.columns, dtype=float)
    rows = []
    for position, date in enumerate(scores.index):
        if position % rebalance_interval != 0:
            action = "hold_schedule"
        elif pd.to_numeric(scores.loc[date], errors="coerce").dropna().nunique() <= 1:
            action = "hold_invalid"
        else:
            previous = buffered_cross_section(
                scores.loc[date],
                previous,
                entry_quantile=entry_quantile,
                exit_quantile=exit_quantile,
                max_weight=max_weight,
            )
            action = "rebalance"
        targets.loc[date] = previous
        rows.append({"date": date, "action": action})
    diagnostics = pd.DataFrame(rows).set_index("date")
    diagnostics.index.name = None
    return BufferedTargetResult(targets=targets, diagnostics=diagnostics)


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


@dataclass(frozen=True)
class LiquidityCostModel:
    """Per-name trading-cost model: flat half-spread plus square-root impact.

    ``half_spread_bps`` is a fixed cost proportional to traded weight. Market
    impact follows the standard square-root law: the cost of trading a name on a
    date scales with ``participation ** impact_exponent``, where participation is
    the traded notional divided by the name's dollar ADV. ``participation_cap``
    bounds the modelled participation (trades above it are clipped for the cost
    estimate, and reported separately as a capacity breach). Names with missing
    or non-positive ADV are treated as fully illiquid — their participation is
    the cap whenever they are traded.
    """

    half_spread_bps: float = 5.0
    impact_coef_bps: float = 100.0
    impact_exponent: float = 0.5
    participation_cap: float = 0.20

    def __post_init__(self) -> None:
        if self.half_spread_bps < 0 or self.impact_coef_bps < 0:
            raise ValueError("cost coefficients must be non-negative")
        if not 0 < self.impact_exponent <= 1:
            raise ValueError("impact_exponent must be within (0, 1]")
        if not 0 < self.participation_cap <= 1:
            raise ValueError("participation_cap must be within (0, 1]")


def participation_rates(
    weight_changes: pd.DataFrame,
    adv_dollar: pd.DataFrame,
    aum: float,
) -> pd.DataFrame:
    """Traded notional divided by dollar ADV, per name per date.

    Non-positive or missing ADV maps to ``inf`` on traded cells (fully illiquid)
    and to ``0`` where nothing is traded.
    """
    if aum <= 0:
        raise ValueError("aum must be positive")
    dw = weight_changes.abs()
    adv = adv_dollar.reindex(index=dw.index, columns=dw.columns)
    adv = adv.where(adv > 0)
    traded_notional = dw * aum
    participation = traded_notional.div(adv)
    participation = participation.where(dw > 0, 0.0)
    # Traded names with missing/zero ADV are fully illiquid: infinite participation
    # (clipped to the cap when costed, and counted as a capacity breach).
    illiquid = (dw > 0) & adv.isna()
    participation = participation.mask(illiquid, np.inf)
    return participation


def liquidity_trade_costs(
    weight_changes: pd.DataFrame,
    adv_dollar: pd.DataFrame,
    aum: float,
    model: LiquidityCostModel,
) -> pd.DataFrame:
    """Per-name, per-date trading cost as a fraction of portfolio NAV."""
    dw = weight_changes.abs()
    participation = participation_rates(weight_changes, adv_dollar, aum)
    capped = participation.clip(upper=model.participation_cap)
    spread = (model.half_spread_bps / 10_000.0) * dw
    impact = (model.impact_coef_bps / 10_000.0) * capped.pow(model.impact_exponent) * dw
    return spread.add(impact, fill_value=0.0).fillna(0.0)


def _executed_and_turnover(
    target_weights: pd.DataFrame, asset_returns: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    targets = target_weights.reindex(
        index=asset_returns.index, columns=asset_returns.columns
    ).fillna(0.0)
    executed = targets.shift(1).fillna(0.0)
    weight_changes = executed.diff()
    weight_changes.iloc[0] = executed.iloc[0]
    turnover = weight_changes.abs().sum(axis=1)
    return executed, weight_changes, turnover


def _performance_metrics(net: pd.Series) -> dict[str, float]:
    wealth = (1.0 + net).cumprod()
    drawdown = wealth.div(wealth.cummax()).sub(1.0)
    annualized_return = float(net.mean() * 252)
    annualized_volatility = float(net.std(ddof=1) * np.sqrt(252)) if len(net) > 1 else 0.0
    return {
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe": annualized_return / annualized_volatility
        if annualized_volatility > 0
        else np.nan,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else np.nan,
    }


def simulate_portfolio_liquidity_aware(
    target_weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    adv_dollar: pd.DataFrame,
    aum: float,
    model: LiquidityCostModel | None = None,
) -> PortfolioResult:
    """Simulate net returns with per-name spread + square-root impact costs.

    Unlike :func:`simulate_portfolio`'s flat ``cost_bps``, the cost of each
    trade depends on how large it is relative to the name's dollar ADV at the
    modelled ``aum``, so the same signal gets progressively more expensive as
    capital scales — the mechanism a flat bps model cannot express.
    """
    model = LiquidityCostModel() if model is None else model
    executed, weight_changes, turnover = _executed_and_turnover(
        target_weights, asset_returns
    )
    returns = asset_returns.fillna(0.0)
    gross = (executed * returns).sum(axis=1)
    costs = liquidity_trade_costs(weight_changes, adv_dollar, aum, model)
    date_cost = costs.sum(axis=1)
    net = gross - date_cost

    metrics = _performance_metrics(net)
    participation = participation_rates(weight_changes, adv_dollar, aum)
    traded = weight_changes.abs() > 0
    breaches = int(((participation > model.participation_cap) & traded).to_numpy().sum())
    metrics.update(
        {
            "average_turnover": float(turnover.mean()),
            "total_cost": float(date_cost.sum()),
            "average_daily_cost": float(date_cost.mean()),
            "aum": float(aum),
            "max_participation": float(
                participation.where(traded).replace([np.inf], np.nan).max().max()
            ),
            "capacity_breach_trades": breaches,
        }
    )
    return PortfolioResult(executed, gross, net, turnover, metrics)


def capacity_curve(
    target_weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    adv_dollar: pd.DataFrame,
    aum_grid: tuple[float, ...],
    model: LiquidityCostModel | None = None,
) -> pd.DataFrame:
    """Net Sharpe and cost drag across a grid of AUM levels.

    The capacity of a signal is where scaling AUM erodes its net Sharpe; this
    table makes that visible instead of assuming cost is AUM-invariant.
    """
    if not aum_grid:
        raise ValueError("aum_grid must contain at least one level")
    rows = []
    for aum in aum_grid:
        result = simulate_portfolio_liquidity_aware(
            target_weights, asset_returns, adv_dollar, aum, model
        )
        rows.append(
            {
                "aum": float(aum),
                "net_sharpe": result.metrics["sharpe"],
                "annualized_return": result.metrics["annualized_return"],
                "average_daily_cost": result.metrics["average_daily_cost"],
                "max_participation": result.metrics["max_participation"],
                "capacity_breach_trades": result.metrics["capacity_breach_trades"],
            }
        )
    return pd.DataFrame(rows)


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
