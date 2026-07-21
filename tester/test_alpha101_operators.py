import numpy as np
import pandas as pd
import pytest

from factor_section.alpha101 import (
    ALPHA101_REGISTRY,
    adv,
    correlation,
    covariance,
    delay,
    delta,
    rank,
    safe_divide,
    signed_power,
    stddev,
    ts_max,
    ts_min,
    ts_rank,
    ts_sum,
    where,
)


def make_panel(values):
    return pd.DataFrame(
        values,
        index=pd.date_range("2024-01-01", periods=len(values)),
        columns=["A", "B"],
        dtype=float,
    )


def test_cross_sectional_rank_uses_same_date_only():
    panel = make_panel([[1, 3], [4, 2]])

    result = rank(panel)

    expected = make_panel([[0.5, 1.0], [1.0, 0.5]])
    pd.testing.assert_frame_equal(result, expected)


def test_time_series_operators_require_a_full_window():
    panel = make_panel([[1, 5], [2, 4], [3, 3]])

    assert ts_sum(panel, 2).iloc[0].isna().all()
    assert ts_min(panel, 2).iloc[-1].tolist() == [2.0, 3.0]
    assert ts_max(panel, 2).iloc[-1].tolist() == [3.0, 4.0]
    assert ts_rank(panel, 3).iloc[-1].tolist() == [1.0, pytest.approx(1 / 3)]
    assert stddev(panel, 2).iloc[-1, 0] == pytest.approx(np.sqrt(0.5))


def test_delay_delta_correlation_and_covariance_preserve_missing_warmup():
    left = make_panel([[1, 5], [2, 4], [3, 3], [4, 2]])
    right = make_panel([[2, 10], [4, 8], [6, 6], [8, 4]])

    assert delay(left, 1).iloc[0].isna().all()
    pd.testing.assert_frame_equal(delta(left, 1).iloc[1:], make_panel([[1, -1]] * 3).set_axis(left.index[1:]))
    assert correlation(left, right, 3).iloc[-1].tolist() == pytest.approx([1.0, 1.0])
    assert covariance(left, right, 3).iloc[-1].tolist() == pytest.approx([2.0, 2.0])


def test_signed_power_safe_divide_and_where_do_not_turn_invalid_values_into_zero():
    base = make_panel([[-2, 2], [0, np.nan]])
    denominator = make_panel([[2, 0], [0, 1]])
    condition = pd.DataFrame(
        [[True, False], [np.nan, True]], index=base.index, columns=base.columns
    )

    powered = signed_power(base, 2)
    divided = safe_divide(base, denominator)
    selected = where(condition, base, -base)

    assert powered.iloc[0].tolist() == [-4.0, 4.0]
    assert divided.iloc[0, 1] != 0 and np.isnan(divided.iloc[0, 1])
    assert np.isnan(selected.iloc[1, 0])


def test_adv_is_full_window_average_dollar_volume():
    price = make_panel([[2, 4], [3, 5], [4, 6]])
    volume = make_panel([[10, 20], [10, 20], [10, 20]])

    result = adv(price, volume, 2)

    assert result.iloc[0].isna().all()
    assert result.iloc[-1].tolist() == [35.0, 110.0]


def test_registry_contains_only_the_approved_delay_one_formulas():
    expected = {2, 7, 12, 17, 21, 22, 30, 34, 35, 40, 46, 101}

    assert set(ALPHA101_REGISTRY) == expected
    assert all(spec.delay >= 1 for spec in ALPHA101_REGISTRY.values())
    assert len({spec.name for spec in ALPHA101_REGISTRY.values()}) == len(expected)
