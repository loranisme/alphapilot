from __future__ import annotations

import pandas as pd
import pytest

from research_platform.universe import (
    SizeBucket,
    apply_size_bucket,
    resolve_bucket,
    size_bucket_membership,
)


def _size_panel() -> pd.DataFrame:
    dates = pd.to_datetime(["2022-01-03", "2022-01-04"])
    # A largest ... E smallest, stable across the two dates.
    return pd.DataFrame(
        {"A": [500, 500], "B": [400, 400], "C": [300, 300], "D": [200, 200], "E": [100, 100]},
        index=dates,
    )


def test_top_bucket_selects_largest():
    membership = size_bucket_membership(_size_panel(), SizeBucket("top2", "top", n=2))
    row = membership.iloc[0]
    assert set(row.index[row]) == {"A", "B"}


def test_bottom_bucket_selects_smallest():
    membership = size_bucket_membership(_size_panel(), SizeBucket("bot2", "bottom", n=2))
    row = membership.iloc[0]
    assert set(row.index[row]) == {"D", "E"}


def test_quantile_smaller_half():
    bucket = SizeBucket("smaller_half", "quantile", low=0.0, high=0.5)
    membership = size_bucket_membership(_size_panel(), bucket)
    row = membership.iloc[0]
    # 5 names: percentiles ~ [0.9,0.7,0.5,0.3,0.1]; [0,0.5] keeps C,D,E.
    assert set(row.index[row]) == {"C", "D", "E"}


def test_bucket_respects_base_membership():
    panel = _size_panel()
    base = pd.DataFrame(True, index=panel.index, columns=panel.columns)
    base.loc[:, "A"] = False  # A not a PIT member
    membership = size_bucket_membership(panel, SizeBucket("top2", "top", n=2), base_membership=base)
    row = membership.iloc[0]
    assert set(row.index[row]) == {"B", "C"}  # A excluded, next two largest


def test_apply_size_bucket_intersects_with_mask():
    panel = _size_panel()
    mask = pd.DataFrame(True, index=panel.index, columns=panel.columns)
    mask.loc[panel.index[0], "B"] = False  # B not a member on day 0
    result = apply_size_bucket(mask, panel, SizeBucket("top2", "top", n=2))
    day0 = result.iloc[0]
    # top-2 among members on day0 is A and C (B excluded), so result day0 = {A, C}
    assert set(day0.index[day0]) == {"A", "C"}
    day1 = result.iloc[1]
    assert set(day1.index[day1]) == {"A", "B"}


def test_resolve_bucket_and_validation():
    assert resolve_bucket("mega_top100").n == 100
    assert resolve_bucket(SizeBucket("x", "top", n=5)).n == 5
    with pytest.raises(ValueError):
        resolve_bucket("does_not_exist")
    with pytest.raises(ValueError):
        SizeBucket("bad", "top")  # missing n
    with pytest.raises(ValueError):
        SizeBucket("bad", "quantile", low=0.6, high=0.4)  # low >= high
