import numpy as np
import pandas as pd

from research_platform.preprocessing import (
    build_industry_score_variants,
    build_industry_score_variants_panel,
    standardize_series,
)


def test_soft_score_removes_exactly_half_fitted_industry_component():
    tickers = [f"T{i}" for i in range(20)]
    industry = pd.Series(["A"] * 10 + ["B"] * 10, index=tickers)
    score = pd.Series(np.r_[np.arange(10), np.arange(10) + 10.0], index=tickers)

    variants = build_industry_score_variants(
        score, industry, soft_strength=0.5, min_names=10
    )

    expected = standardize_series(score - 0.5 * variants.fitted)
    pd.testing.assert_series_equal(variants.soft, expected)
    assert variants.strict.groupby(industry).mean().abs().max() < 1e-10
    assert variants.diagnostics["valid"] is True


def test_invalid_cross_section_returns_nan_instead_of_raw_fallback():
    score = pd.Series({"A": 1.0, "B": 2.0, "C": np.nan})
    industry = pd.Series({"A": "x", "B": "y", "C": "x"})

    variants = build_industry_score_variants(score, industry, min_names=3)

    assert variants.raw.isna().all()
    assert variants.soft.isna().all()
    assert variants.strict.isna().all()
    assert variants.diagnostics["reason"] == "insufficient_names"


def test_panel_wrapper_preserves_shape_and_date_diagnostics():
    dates = pd.bdate_range("2020-01-01", periods=2)
    names = [f"T{i}" for i in range(10)]
    score = pd.DataFrame(
        [np.arange(10), np.full(10, np.nan)], index=dates, columns=names
    )
    industry = pd.DataFrame(
        [["A"] * 5 + ["B"] * 5] * 2, index=dates, columns=names
    )

    result = build_industry_score_variants_panel(score, industry, min_names=10)

    assert result.raw.shape == score.shape
    assert result.soft.shape == score.shape
    assert result.strict.shape == score.shape
    assert result.diagnostics.loc[dates[0], "valid"]
    assert not result.diagnostics.loc[dates[1], "valid"]
