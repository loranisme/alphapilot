from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_platform.regime import (
    calendar_year_labels,
    group_daily_ic,
    ic_stability_summary,
    subperiod_labels,
    subperiod_performance,
)


def test_calendar_year_labels():
    dates = pd.to_datetime(["2021-06-30", "2022-01-03", "2022-12-30"])
    labels = calendar_year_labels(dates)
    assert list(labels) == ["2021", "2022", "2022"]


def test_subperiod_labels_half_open_buckets():
    dates = pd.bdate_range("2020-01-01", "2023-12-29")
    labels = subperiod_labels(dates, boundaries=["2022-01-01"])
    # Two regimes: before and from 2022.
    assert labels.nunique() == 2
    assert labels.loc[pd.Timestamp("2021-12-31")] != labels.loc[pd.Timestamp("2022-01-03")]


def test_group_daily_ic_means_within_subperiod():
    dates = pd.to_datetime(["2021-01-01", "2021-02-01", "2022-01-01", "2022-02-01"])
    ic = pd.Series([0.1, 0.3, -0.2, -0.4], index=dates)
    labels = calendar_year_labels(dates)
    grouped = group_daily_ic(ic, labels)
    assert grouped.loc["2021"] == pytest.approx(0.2)
    assert grouped.loc["2022"] == pytest.approx(-0.3)
    assert list(grouped.index) == ["2021", "2022"]  # order preserved


def test_ic_stability_summary_flags_sign_flips():
    consistent = pd.Series({"2021": 0.05, "2022": 0.04, "2023": 0.06})
    summary = ic_stability_summary(consistent)
    assert summary["dominant_sign"] == 1
    assert summary["sign_consistency"] == 1.0

    flipping = pd.Series({"2021": 0.05, "2022": -0.04, "2023": 0.06})
    flip_summary = ic_stability_summary(flipping)
    assert flip_summary["dominant_sign"] == 1
    assert flip_summary["sign_consistency"] == pytest.approx(2 / 3)
    assert flip_summary["worst_subperiod"] == "2022"


def test_subperiod_performance_per_regime():
    dates = pd.date_range("2021-01-01", periods=6, freq="D")
    # First 3 days flat-positive, last 3 days negative -> different regimes.
    net = pd.Series([0.01, 0.01, 0.01, -0.02, -0.02, -0.02], index=dates)
    labels = pd.Series(["A", "A", "A", "B", "B", "B"], index=dates, name="subperiod")
    perf = subperiod_performance(net, labels, periods_per_year=252).set_index("subperiod")
    assert perf.loc["A", "annualized_return"] > 0
    assert perf.loc["B", "annualized_return"] < 0
    assert perf.loc["A", "n_obs"] == 3
