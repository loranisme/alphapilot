# tester/test_scorecard.py
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_platform.scorecard import calmar_ratio, sortino_ratio, win_rate


def test_win_rate_is_fraction_of_positive_periods():
    net = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01])
    assert win_rate(net) == pytest.approx(3 / 5)


def test_sortino_uses_downside_deviation_only():
    net = pd.Series([0.01, -0.02, 0.01, -0.01, 0.02])
    downside = pd.Series([min(x, 0.0) for x in net]).std(ddof=1)
    expected = (net.mean() * 252) / (downside * np.sqrt(252))
    assert sortino_ratio(net, periods_per_year=252) == pytest.approx(expected)


def test_sortino_is_nan_without_downside():
    net = pd.Series([0.01, 0.02, 0.03])
    assert np.isnan(sortino_ratio(net))


def test_calmar_is_annual_return_over_abs_drawdown():
    assert calmar_ratio(annualized_return=0.12, max_drawdown=-0.10) == pytest.approx(1.2)
    assert np.isnan(calmar_ratio(0.12, 0.0))


# append to tester/test_scorecard.py
from research_platform.scorecard import build_factor_scorecard


def _monotone_factor_inputs():
    dates = pd.bdate_range("2021-01-01", periods=120)
    tickers = [f"T{i}" for i in range(40)]
    rng = np.random.default_rng(0)
    base = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))), index=dates, columns=tickers)
    # forward return increases with the factor rank -> positive IC, monotone groups
    forward = base.rank(axis=1) / len(tickers) * 0.02 + rng.standard_normal((len(dates), len(tickers))) * 0.001
    return {"good": base}, forward


def test_factor_scorecard_has_expected_columns_and_positive_ic():
    factors, forward = _monotone_factor_inputs()
    table = build_factor_scorecard(
        factors, forward, forward.index, n_groups=5, min_names=10, horizon=5
    ).set_index("factor")
    for col in ["rank_ic", "rank_ic_t", "pearson_ic", "icir", "icir_annualized",
                "ic_hit_rate", "monotonicity", "long_short_t", "rank_turnover",
                "coverage", "n_obs"]:
        assert col in table.columns
    assert table.loc["good", "rank_ic"] > 0
    assert table.loc["good", "monotonicity"] > 0.5
    assert 0.0 <= table.loc["good", "ic_hit_rate"] <= 1.0
