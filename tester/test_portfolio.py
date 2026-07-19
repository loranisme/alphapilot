from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_platform.portfolio import (
    build_long_short_weights,
    simulate_portfolio,
)


def test_cost_is_turnover_times_one_way_bps():
    dates = pd.bdate_range("2024-01-02", periods=3)
    targets = pd.DataFrame(
        [[0.5, -0.5], [-0.5, 0.5], [-0.5, 0.5]],
        index=dates,
        columns=["A", "B"],
    )
    returns = pd.DataFrame(0.0, index=dates, columns=targets.columns)
    result = simulate_portfolio(targets, returns, cost_bps=10.0)
    assert result.turnover.iloc[2] == pytest.approx(2.0)
    assert result.net_returns.iloc[2] == pytest.approx(-0.002)


def test_portfolio_executes_signal_on_next_date():
    dates = pd.bdate_range("2024-01-02", periods=2)
    targets = pd.DataFrame([[1.0], [0.0]], index=dates, columns=["A"])
    returns = pd.DataFrame([[0.10], [0.20]], index=dates, columns=["A"])
    result = simulate_portfolio(targets, returns, cost_bps=0.0)
    assert result.gross_returns.iloc[0] == 0.0
    assert result.gross_returns.iloc[1] == pytest.approx(0.20)


def test_equal_weight_builder_is_dollar_neutral_and_capped():
    signal = pd.Series(np.arange(20, dtype=float), index=[f"T{i}" for i in range(20)])
    weights = build_long_short_weights(signal, quantile=0.2, max_weight=0.30)
    assert weights.sum() == pytest.approx(0.0)
    assert weights.clip(lower=0).sum() == pytest.approx(1.0)
    assert -weights.clip(upper=0).sum() == pytest.approx(1.0)
    assert weights.abs().max() <= 0.30 + 1e-12


def test_industry_neutral_builder_balances_each_industry():
    tickers = [f"T{i}" for i in range(20)]
    signal = pd.Series(np.arange(20, dtype=float), index=tickers)
    industry = pd.Series(["A"] * 10 + ["B"] * 10, index=tickers)
    weights = build_long_short_weights(
        signal, quantile=0.2, max_weight=0.30, industry=industry
    )
    assert weights.groupby(industry).sum().abs().max() < 1e-12


def test_metrics_include_drawdown_and_sharpe():
    dates = pd.bdate_range("2024-01-02", periods=4)
    targets = pd.DataFrame([[1.0], [1.0], [1.0], [1.0]], index=dates, columns=["A"])
    returns = pd.DataFrame([[0.0], [0.1], [-0.2], [0.1]], index=dates, columns=["A"])
    result = simulate_portfolio(targets, returns, cost_bps=0.0)
    assert result.metrics["max_drawdown"] < 0
    assert "sharpe" in result.metrics
