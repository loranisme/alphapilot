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
