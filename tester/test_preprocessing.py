from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_platform.preprocessing import (
    neutralize_cross_section,
    neutralize_panel,
    standardize_panel,
    winsorize_panel,
)


def test_neutralizer_removes_industry_and_size_exposure():
    tickers = [f"T{i}" for i in range(12)]
    industry = pd.Series(["A"] * 6 + ["B"] * 6, index=tickers)
    market_cap = pd.Series(np.exp(np.linspace(1, 3, 12)), index=tickers)
    idiosyncratic = pd.Series(np.tile([-1.0, 1.0], 6), index=tickers)
    signal = 3.0 * (industry == "B").astype(float) + 2.0 * np.log(market_cap) + idiosyncratic
    result = neutralize_cross_section(signal, industry, market_cap, min_names=10)
    assert result.diagnostics["valid"]
    assert result.values.groupby(industry).mean().abs().max() < 1e-10
    assert abs(result.values.corr(np.log(market_cap))) < 1e-10


def test_neutralizer_marks_insufficient_cross_section_invalid():
    signal = pd.Series([1.0, 2.0], index=["A", "B"])
    industry = pd.Series(["Tech", "Energy"], index=["A", "B"])
    result = neutralize_cross_section(signal, industry, min_names=3)
    assert not result.diagnostics["valid"]
    assert result.diagnostics["reason"] == "insufficient_names"
    assert result.values.isna().all()


def test_standardization_is_date_local():
    panel = pd.DataFrame(
        [[1.0, 2.0, 3.0], [100.0, 200.0, 300.0]],
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        columns=list("ABC"),
    )
    standardized = standardize_panel(panel)
    assert standardized.mean(axis=1).abs().max() < 1e-12
    assert np.allclose(standardized.std(axis=1, ddof=0), 1.0)


def test_winsorization_does_not_mutate_input():
    panel = pd.DataFrame(
        [[0.0, 1.0, 100.0]],
        index=pd.to_datetime(["2024-01-02"]),
        columns=list("ABC"),
    )
    original = panel.copy()
    result = winsorize_panel(panel, lower_q=0.1, upper_q=0.9)
    pd.testing.assert_frame_equal(panel, original)
    assert result.loc[result.index[0], "C"] < 100.0


def test_neutralize_panel_reports_daily_coverage():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
    tickers = [f"T{i}" for i in range(10)]
    signal = pd.DataFrame(np.arange(20).reshape(2, 10), index=dates, columns=tickers)
    industry = pd.DataFrame(
        [["A"] * 5 + ["B"] * 5, ["A"] * 5 + ["B"] * 4 + [None]],
        index=dates,
        columns=tickers,
    )
    result = neutralize_panel(signal, industry, min_names=5)
    assert list(result.diagnostics.index) == list(dates)
    assert result.diagnostics.loc[dates[0], "coverage"] == pytest.approx(1.0)
    assert result.diagnostics.loc[dates[1], "coverage"] == pytest.approx(0.9)
