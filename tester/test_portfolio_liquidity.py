from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_platform.portfolio import (
    LiquidityCostModel,
    capacity_curve,
    liquidity_trade_costs,
    participation_rates,
    simulate_portfolio,
    simulate_portfolio_liquidity_aware,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=n)


def test_participation_rate_math_and_illiquid_names():
    dates = _dates(1)
    dw = pd.DataFrame({"A": [0.5], "B": [0.0], "C": [0.5]}, index=dates)
    adv = pd.DataFrame({"A": [1e9], "B": [1e9], "C": [0.0]}, index=dates)
    p = participation_rates(dw, adv, aum=1e7)
    assert p.loc[dates[0], "A"] == pytest.approx(0.5 * 1e7 / 1e9)  # 0.005
    assert p.loc[dates[0], "B"] == 0.0  # nothing traded
    assert np.isinf(p.loc[dates[0], "C"])  # traded but zero ADV -> fully illiquid


def test_liquidity_trade_costs_spread_plus_sqrt_impact():
    dates = _dates(1)
    dw = pd.DataFrame({"A": [0.5]}, index=dates)
    adv = pd.DataFrame({"A": [1e9]}, index=dates)
    model = LiquidityCostModel(
        half_spread_bps=5.0, impact_coef_bps=100.0, impact_exponent=0.5, participation_cap=0.20
    )
    costs = liquidity_trade_costs(dw, adv, aum=1e7, model=model)
    p = 0.5 * 1e7 / 1e9
    expected = (5.0 / 1e4) * 0.5 + (100.0 / 1e4) * (p**0.5) * 0.5
    assert costs.loc[dates[0], "A"] == pytest.approx(expected)


def test_participation_cap_clips_impact():
    dates = _dates(1)
    dw = pd.DataFrame({"A": [0.5]}, index=dates)
    adv = pd.DataFrame({"A": [1.0]}, index=dates)  # microscopic ADV -> huge participation
    model = LiquidityCostModel(participation_cap=0.10)
    costs = liquidity_trade_costs(dw, adv, aum=1e7, model=model)
    capped = (5.0 / 1e4) * 0.5 + (100.0 / 1e4) * (0.10**0.5) * 0.5
    assert costs.loc[dates[0], "A"] == pytest.approx(capped)


def test_liquidity_cost_rises_with_aum_and_erodes_returns():
    dates = _dates(6)
    asset_returns = pd.DataFrame(
        {"A": [0.01] * 6, "B": [-0.01] * 6}, index=dates
    )
    targets = pd.DataFrame({"A": [0.5] * 6, "B": [-0.5] * 6}, index=dates)
    adv = pd.DataFrame({"A": [5e7] * 6, "B": [5e7] * 6}, index=dates)
    small = simulate_portfolio_liquidity_aware(targets, asset_returns, adv, aum=1e6)
    large = simulate_portfolio_liquidity_aware(targets, asset_returns, adv, aum=1e9)
    assert large.metrics["total_cost"] > small.metrics["total_cost"]
    assert large.metrics["annualized_return"] < small.metrics["annualized_return"]
    assert large.metrics["max_participation"] > small.metrics["max_participation"]


def test_deep_liquidity_collapses_to_spread_only():
    dates = _dates(4)
    asset_returns = pd.DataFrame({"A": [0.0] * 4, "B": [0.0] * 4}, index=dates)
    targets = pd.DataFrame({"A": [0.5] * 4, "B": [-0.5] * 4}, index=dates)
    adv = pd.DataFrame({"A": [1e18] * 4, "B": [1e18] * 4}, index=dates)  # effectively infinite
    model = LiquidityCostModel(half_spread_bps=5.0, impact_coef_bps=100.0)
    result = simulate_portfolio_liquidity_aware(targets, asset_returns, adv, aum=1e7, model=model)
    turnover_total = result.turnover.sum()
    spread_only = (model.half_spread_bps / 1e4) * turnover_total
    assert result.metrics["total_cost"] == pytest.approx(spread_only, rel=1e-3)


def test_capacity_curve_is_monotone_in_cost():
    dates = _dates(8)
    asset_returns = pd.DataFrame(
        {"A": [0.01] * 8, "B": [-0.01] * 8}, index=dates
    )
    targets = pd.DataFrame({"A": [0.5] * 8, "B": [-0.5] * 8}, index=dates)
    adv = pd.DataFrame({"A": [1e7] * 8, "B": [1e7] * 8}, index=dates)
    curve = capacity_curve(targets, asset_returns, adv, aum_grid=(1e5, 1e7, 1e9))
    assert list(curve["aum"]) == [1e5, 1e7, 1e9]
    assert curve["average_daily_cost"].is_monotonic_increasing
    assert curve["annualized_return"].is_monotonic_decreasing


def test_liquidity_aware_matches_frictionless_gross():
    dates = _dates(5)
    asset_returns = pd.DataFrame({"A": [0.01] * 5, "B": [-0.02] * 5}, index=dates)
    targets = pd.DataFrame({"A": [0.5] * 5, "B": [-0.5] * 5}, index=dates)
    adv = pd.DataFrame({"A": [1e12] * 5, "B": [1e12] * 5}, index=dates)
    liq = simulate_portfolio_liquidity_aware(targets, asset_returns, adv, aum=1e6)
    flat = simulate_portfolio(targets, asset_returns, cost_bps=0.0)
    pd.testing.assert_series_equal(liq.gross_returns, flat.gross_returns)


def test_invalid_inputs_raise():
    dates = _dates(1)
    dw = pd.DataFrame({"A": [0.5]}, index=dates)
    adv = pd.DataFrame({"A": [1e9]}, index=dates)
    with pytest.raises(ValueError):
        participation_rates(dw, adv, aum=0.0)
    with pytest.raises(ValueError):
        LiquidityCostModel(impact_exponent=1.5)
    with pytest.raises(ValueError):
        LiquidityCostModel(participation_cap=0.0)
