import numpy as np
import pandas as pd
import pytest

from research_platform.selection import select_correlation_aware_factors


def make_selection_fixture(periods=100, names=12):
    dates = pd.bdate_range("2020-01-01", periods=periods)
    tickers = [f"T{i}" for i in range(names)]
    rng = np.random.default_rng(7)
    factors = {}
    for number in range(6):
        factors[f"f{number}"] = pd.DataFrame(
            rng.normal(size=(periods, names)), index=dates, columns=tickers
        )
    factors["f1"] = factors["f0"] * 2.0
    ic = pd.DataFrame(
        {
            "f0": 0.030 + rng.normal(0, 0.001, periods),
            "f1": 0.020 + rng.normal(0, 0.001, periods),
            "f2": 0.025 + rng.normal(0, 0.001, periods),
            "f3": -0.022 + rng.normal(0, 0.001, periods),
            "f4": 0.018 + rng.normal(0, 0.001, periods),
            "f5": 0.016 + rng.normal(0, 0.001, periods),
        },
        index=dates,
    )
    return dates, factors, ic


def select_fixture(factors, ic, train_dates):
    return select_correlation_aware_factors(
        factors,
        ic,
        train_dates,
        min_names=10,
        min_block_observations=15,
        min_pair_dates=40,
    )


def test_selector_keeps_best_cluster_representative_and_five_factor_portfolio():
    dates, factors, ic = make_selection_fixture()

    result = select_fixture(factors, ic, dates[:80])

    assert result.valid
    assert len(result.selected) == 5
    assert "f0" in result.selected
    assert "f1" not in result.selected
    assert result.directions["f3"] == -1
    assert sum(result.weights.values()) == pytest.approx(1.0)
    assert max(result.weights.values()) <= 0.20 + 1e-12
    assert set(
        [
            "base_quality",
            "rank_turnover",
            "cost_adjusted_quality",
            "redundancy_penalty",
            "adjusted_quality",
        ]
    ).issubset(result.diagnostics.columns)


def test_selector_records_coverage_failure_instead_of_backfilling():
    dates, factors, ic = make_selection_fixture()
    factors["f5"].loc[dates[:50]] = np.nan

    result = select_fixture(factors, ic, dates[:80])

    assert not bool(result.diagnostics.loc["f5", "eligible"])
    assert "coverage_below_80pct" in result.diagnostics.loc["f5", "ineligible_reason"]
    assert not result.valid
    assert result.weights == {}


def test_selector_is_unchanged_when_only_oos_values_are_perturbed():
    dates, factors, ic = make_selection_fixture()
    original = select_fixture(factors, ic, dates[:80])
    changed_factors = {name: panel.copy() for name, panel in factors.items()}
    changed_ic = ic.copy()
    for panel in changed_factors.values():
        panel.loc[dates[80:]] *= 1_000.0
    changed_ic.loc[dates[80:]] *= -100.0

    changed = select_fixture(changed_factors, changed_ic, dates[:80])

    assert original.selected == changed.selected
    assert original.directions == changed.directions
    assert original.weights == changed.weights
    pd.testing.assert_frame_equal(
        original.factor_value_correlation.values,
        changed.factor_value_correlation.values,
    )
    pd.testing.assert_frame_equal(original.ic_correlation.values, changed.ic_correlation.values)
    pd.testing.assert_frame_equal(original.clusters, changed.clusters)
