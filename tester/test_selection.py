import numpy as np
import pandas as pd
import pytest

from research_platform.selection import capped_simplex_weights, select_stable_factors


def test_negative_ic_factor_is_flipped_and_receives_non_negative_weight():
    dates = pd.bdate_range("2020-01-01", periods=80)
    ic = pd.DataFrame({"negative": -0.03, "positive": 0.02}, index=dates)

    result = select_stable_factors(ic, max_weight=0.60)

    assert result.directions == {"negative": -1, "positive": 1}
    assert all(weight >= 0 for weight in result.weights.values())
    assert sum(result.weights.values()) == pytest.approx(1.0)


def test_factor_failing_three_of_four_blocks_is_excluded():
    dates = pd.bdate_range("2020-01-01", periods=80)
    values = np.r_[
        np.full(20, 0.02),
        np.full(20, -0.02),
        np.full(20, 0.02),
        np.full(20, -0.02),
    ]

    result = select_stable_factors(
        pd.DataFrame({"unstable": values}, index=dates), max_weight=1.0
    )

    assert "unstable" not in result.selected


def test_recent_half_direction_mismatch_is_excluded():
    dates = pd.bdate_range("2020-01-01", periods=80)
    values = np.r_[np.full(60, 0.03), np.full(20, -0.08)]

    result = select_stable_factors(
        pd.DataFrame({"reversed": values}, index=dates), max_weight=1.0
    )

    assert not bool(result.diagnostics.loc["reversed", "recent_agrees"])
    assert "reversed" not in result.selected


def test_capped_weights_redistribute_and_reject_infeasible_cap():
    quality = pd.Series({"a": 10.0, "b": 1.0, "c": 1.0})

    weights = capped_simplex_weights(quality, cap=0.50)

    assert weights["a"] == pytest.approx(0.50)
    assert weights["b"] == pytest.approx(0.25)
    assert weights["c"] == pytest.approx(0.25)
    with pytest.raises(ValueError, match="infeasible"):
        capped_simplex_weights(quality, cap=0.20)


def test_no_factor_family_is_backfilled_when_it_fails_rules():
    dates = pd.bdate_range("2020-01-01", periods=80)
    ic = pd.DataFrame(
        {
            "stable_a": 0.03,
            "stable_b": 0.02,
            "weak_family_member": 0.001,
        },
        index=dates,
    )

    result = select_stable_factors(ic, max_weight=0.60)

    assert result.selected == ("stable_a", "stable_b")
