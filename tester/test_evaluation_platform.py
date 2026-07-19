from __future__ import annotations

import numpy as np
import pandas as pd

from research_platform.evaluation import (
    evaluate_ic,
    generate_purged_folds,
    run_quantile_backtest,
)


def test_purged_folds_leave_horizon_gap():
    dates = pd.bdate_range("2023-01-02", periods=80)
    folds = generate_purged_folds(
        dates,
        min_train=40,
        test_size=10,
        step=10,
        horizon=5,
        embargo=2,
    )
    train, test = folds[0]
    assert dates.get_loc(test[0]) - dates.get_loc(train[-1]) >= 6
    assert set(train).isdisjoint(test)


def test_purged_folds_reject_purge_shorter_than_horizon():
    dates = pd.bdate_range("2023-01-02", periods=80)
    try:
        generate_purged_folds(dates, 40, 10, 10, horizon=5, purge=4)
    except ValueError as exc:
        assert "purge" in str(exc)
    else:
        raise AssertionError("expected purge validation")


def test_evaluate_ic_recovers_positive_rank_signal():
    dates = pd.bdate_range("2024-01-02", periods=5)
    tickers = list("ABCDE")
    values = np.tile(np.arange(5, dtype=float), (5, 1))
    factor = pd.DataFrame(values, index=dates, columns=tickers)
    forward = pd.DataFrame(values * 0.01, index=dates, columns=tickers)
    result = evaluate_ic(factor, forward, method="spearman", min_names=5)
    assert np.allclose(result.to_numpy(), 1.0, atol=1e-12)


def test_quantile_backtest_is_monotonic_for_known_signal():
    dates = pd.bdate_range("2024-01-02", periods=5)
    tickers = [f"T{i}" for i in range(10)]
    values = np.tile(np.arange(10, dtype=float), (5, 1))
    factor = pd.DataFrame(values, index=dates, columns=tickers)
    forward = pd.DataFrame(values * 0.001, index=dates, columns=tickers)
    result = run_quantile_backtest(factor, forward, n_groups=5, min_names=10)
    assert result.group_returns.mean().is_monotonic_increasing
    assert (result.spread > 0).all()
