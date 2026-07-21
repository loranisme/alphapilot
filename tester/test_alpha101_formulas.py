import numpy as np
import pandas as pd

from factor_section.alpha101 import ALPHA101_REGISTRY, build_alpha101_factors
from research_platform.market_data import ResearchOHLCVBundle


def make_bundle(periods=90, names=5):
    dates = pd.bdate_range("2023-01-02", periods=periods)
    frames = {}
    for number in range(names):
        trend = np.arange(periods, dtype=float)
        close = 50.0 + number * 3.0 + trend * (0.10 + number * 0.01)
        open_ = close - np.sin(trend / (3.0 + number)) * 0.5
        frames[f"T{number}"] = pd.DataFrame(
            {
                "Open": open_,
                "High": np.maximum(open_, close) + 1.0 + number * 0.01,
                "Low": np.minimum(open_, close) - 1.0 - number * 0.01,
                "Close": close,
                "Volume": 1_000.0 + trend * (2.0 + number) + (trend % 7) * 11.0,
            },
            index=dates,
        )
    return ResearchOHLCVBundle(frames=frames, metadata={})


def test_builder_returns_exact_curated_registry_in_stable_order():
    factors = build_alpha101_factors(make_bundle())

    assert tuple(factors) == tuple(spec.name for spec in ALPHA101_REGISTRY.values())
    assert all(callable(spec.compute) for spec in ALPHA101_REGISTRY.values())


def test_all_formulas_align_and_leave_warmup_missing_without_infinities():
    bundle = make_bundle()

    factors = build_alpha101_factors(bundle)

    expected_index = next(iter(bundle.frames.values())).index
    expected_columns = pd.Index(bundle.frames)
    for name, factor in factors.items():
        assert factor.index.equals(expected_index)
        assert factor.columns.equals(expected_columns)
        if next(spec for spec in ALPHA101_REGISTRY.values() if spec.name == name).lookback > 1:
            assert factor.iloc[0].isna().all()
        assert not np.isinf(factor.to_numpy(dtype=float, na_value=np.nan)).any()
        assert factor.notna().any().any()


def test_alpha12_and_alpha101_match_the_paper_expressions():
    bundle = make_bundle(periods=4, names=2)
    factors = build_alpha101_factors(bundle)
    close = pd.DataFrame(
        {ticker: frame["Close"] for ticker, frame in bundle.frames.items()}
    )
    open_ = pd.DataFrame(
        {ticker: frame["Open"] for ticker, frame in bundle.frames.items()}
    )
    high = pd.DataFrame(
        {ticker: frame["High"] for ticker, frame in bundle.frames.items()}
    )
    low = pd.DataFrame(
        {ticker: frame["Low"] for ticker, frame in bundle.frames.items()}
    )
    volume = pd.DataFrame(
        {ticker: frame["Volume"] for ticker, frame in bundle.frames.items()}
    )
    dollar_volume = ((open_ + high + low + close) / 4.0) * volume

    expected_12 = np.sign(dollar_volume.diff()) * (-close.diff())
    expected_101 = (close - open_) / ((high - low) + 0.001)

    pd.testing.assert_frame_equal(factors["alpha101_012"], expected_12)
    pd.testing.assert_frame_equal(factors["alpha101_101"], expected_101)


def test_future_ohlcv_perturbation_does_not_change_past_formula_values():
    original_bundle = make_bundle()
    changed_frames = {name: frame.copy() for name, frame in original_bundle.frames.items()}
    cutoff = next(iter(changed_frames.values())).index[69]
    for frame in changed_frames.values():
        future = frame.index > cutoff
        frame.loc[future, ["Open", "High", "Low", "Close"]] *= 100.0
        frame.loc[future, "Volume"] *= 1_000.0

    original = build_alpha101_factors(original_bundle)
    changed = build_alpha101_factors(
        ResearchOHLCVBundle(frames=changed_frames, metadata={})
    )

    for name in original:
        pd.testing.assert_frame_equal(original[name].loc[:cutoff], changed[name].loc[:cutoff])
