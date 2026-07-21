import numpy as np
import pandas as pd
import pytest

from research_platform.correlation import (
    connected_correlation_clusters,
    factor_rank_turnover,
    factor_value_correlation,
    ic_correlation,
)


def test_factor_value_correlation_aligns_direction_and_uses_date_median():
    dates = pd.bdate_range("2024-01-01", periods=4)
    names = ["A", "B", "C", "D"]
    base = pd.DataFrame(
        np.tile([1.0, 2.0, 3.0, 4.0], (len(dates), 1)),
        index=dates,
        columns=names,
    )
    factors = {"up": base, "scaled": base * 3.0, "down": -base}

    estimate = factor_value_correlation(
        factors,
        dates,
        directions={"up": 1, "scaled": 1, "down": -1},
        min_names=4,
        min_dates=3,
    )

    assert estimate.values.loc["up", "scaled"] == pytest.approx(1.0)
    assert estimate.values.loc["up", "down"] == pytest.approx(1.0)
    assert bool(estimate.verified.loc["up", "down"])
    assert estimate.counts.loc["up", "down"] == 4


def test_connected_components_are_transitive_and_unverified_pairs_connect():
    names = ["a", "b", "c", "d"]
    values = pd.DataFrame(
        [
            [1.0, 0.8, 0.1, 0.0],
            [0.8, 1.0, 0.8, 0.0],
            [0.1, 0.8, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        index=names,
        columns=names,
    )
    verified = pd.DataFrame(True, index=names, columns=names)
    verified.loc["c", "d"] = False
    verified.loc["d", "c"] = False

    clusters = connected_correlation_clusters(values, verified, threshold=0.75)

    assert clusters.set_index("factor")["cluster"].nunique() == 1
    assert clusters["factor"].tolist() == sorted(names)


def test_ic_correlation_applies_fixed_diagonal_shrinkage():
    dates = pd.bdate_range("2024-01-01", periods=10)
    values = np.arange(10, dtype=float)
    ic = pd.DataFrame({"a": values, "b": -values}, index=dates)

    estimate = ic_correlation(ic, directions={"a": 1, "b": -1}, shrinkage=0.5)

    assert estimate.values.loc["a", "a"] == 1.0
    assert estimate.values.loc["a", "b"] == pytest.approx(0.5)
    assert estimate.counts.loc["a", "b"] == 10


def test_rank_turnover_uses_only_five_day_rebalance_dates():
    dates = pd.bdate_range("2024-01-01", periods=6)
    panel = pd.DataFrame(
        [
            [1.0, 2.0, 3.0, 4.0],
            [4.0, 1.0, 2.0, 3.0],
            [2.0, 4.0, 1.0, 3.0],
            [3.0, 2.0, 4.0, 1.0],
            [1.0, 3.0, 4.0, 2.0],
            [4.0, 3.0, 2.0, 1.0],
        ],
        index=dates,
        columns=list("ABCD"),
    )

    turnover = factor_rank_turnover(
        panel, dates, rebalance_interval=5, min_names=4
    )

    assert turnover == pytest.approx(0.5)
