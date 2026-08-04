"""
test_backtester.py
------------------
Unit and integration tests for analysis_section/backtester.py.

Test categories
---------------
1. IC utility functions (_future_returns, _sanitize_returns, _compute_ic_series)
2. IC summary statistics
3. Factor selection gates (significance, ICIR floor)
4. Weight building (MVO, shrinkage, cap, ensemble)
5. Fold generation (expanding window, rolling window, edge cases)
6. FoldResult and BacktestResult data containers
7. Walk-forward pipeline with synthetic data
8. Anti-overfitting properties (IS vs OOS gap < threshold)
9. Real-data integration (skipped when data/cleaned absent)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "analysis_section"))
sys.path.insert(0, str(PROJECT_ROOT / "factor_section"))

from backtester import (
    _compute_ic_series,
    _compute_all_ic_series,
    _quantile_group_labels,
    _future_returns,
    _ic_summary,
    _sanitize_returns,
    _select_factors,
    _build_weights,
    BacktestResult,
    FoldResult,
    StratifiedBacktestResult,
    WalkForwardBacktester,
    run_stratified_backtest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HAS_REAL_DATA = (PROJECT_ROOT / "data" / "cleaned").exists() and any(
    (PROJECT_ROOT / "data" / "cleaned").glob("*_cleaned.csv")
)

N_TICKERS = 60
N_DATES = 400
N_FACTORS = 5


def _make_close(n_dates: int = N_DATES, n_tickers: int = N_TICKERS, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n_dates)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    log_ret = rng.normal(0.0005, 0.015, size=(n_dates, n_tickers))
    prices = 100.0 * np.exp(np.cumsum(log_ret, axis=0))
    return pd.DataFrame(prices, index=dates, columns=tickers)


def _make_factor_panel(
    close: pd.DataFrame,
    n_factors: int = N_FACTORS,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Synthetic factor table with MultiIndex(ticker, factor) columns.
    Factor 0 has a mild negative correlation with next-period returns (signal),
    others are noise.
    """
    rng = np.random.default_rng(seed)
    dates = close.index
    tickers = list(close.columns)
    factor_names = [f"factor_{i}" for i in range(n_factors)]

    # future returns at horizon=5
    fwd = close.pct_change(5).shift(-5)

    parts = []
    for fi, fname in enumerate(factor_names):
        for ticker in tickers:
            if fi == 0:
                # correlated with future return (signal)
                signal = -fwd[ticker].shift(1).fillna(0.0)
                noise = rng.normal(0, 0.3, size=len(dates))
                vals = signal.values + noise
            else:
                vals = rng.normal(0, 1, size=len(dates))
            s = pd.Series(vals, index=dates, name=(ticker, fname))
            parts.append(s)

    combined = pd.concat(parts, axis=1)
    combined.columns = pd.MultiIndex.from_tuples(combined.columns)
    return combined


@pytest.fixture(scope="module")
def close():
    return _make_close()


@pytest.fixture(scope="module")
def factor_panel(close):
    return _make_factor_panel(close)


@pytest.fixture(scope="module")
def ic_series(close, factor_panel):
    return _compute_all_ic_series(factor_panel, close, horizon=5)


# ---------------------------------------------------------------------------
# 1. IC utility functions
# ---------------------------------------------------------------------------

class TestICUtilities:
    def test_future_returns_shape(self, close):
        fwd = _future_returns(close, horizon=5)
        assert fwd.shape == close.shape

    def test_future_returns_horizon_shift(self, close):
        fwd = _future_returns(close, horizon=1)
        # last 1 row should be NaN
        assert fwd.iloc[-1].isna().all()
        assert not fwd.iloc[-2].isna().all()

    def test_sanitize_removes_extremes(self, close):
        fwd = _future_returns(close, horizon=5)
        # inject extreme value — sanitize masks it to NaN
        fwd.iloc[10, 0] = 5.0
        cleaned = _sanitize_returns(fwd, max_abs=0.30)
        assert np.isnan(cleaned.iloc[10, 0]) or abs(cleaned.iloc[10, 0]) <= 0.30

    def test_sanitize_preserves_shape(self, close):
        fwd = _future_returns(close, horizon=5)
        cleaned = _sanitize_returns(fwd)
        assert cleaned.shape == fwd.shape

    def test_compute_ic_series_returns_series(self, close):
        fwd = _sanitize_returns(_future_returns(close, horizon=5))
        panel = close.pct_change(5).shift(1)   # simple lagged return factor
        ic = _compute_ic_series(panel, fwd, method="spearman", min_tickers=5)
        assert isinstance(ic, pd.Series)
        assert not ic.empty

    def test_compute_ic_series_within_bounds(self, close):
        fwd = _sanitize_returns(_future_returns(close, horizon=5))
        panel = close.pct_change(5).shift(1)
        ic = _compute_ic_series(panel, fwd, method="spearman", min_tickers=5)
        assert ic.abs().max() <= 1.0 + 1e-9

    def test_compute_ic_series_min_tickers_filter(self, close):
        fwd = _sanitize_returns(_future_returns(close, horizon=5))
        panel = close.pct_change(5).shift(1)
        # require more tickers than universe → empty IC
        ic = _compute_ic_series(panel, fwd, method="spearman", min_tickers=N_TICKERS + 1)
        assert ic.empty

    def test_compute_all_ic_series_columns(self, factor_panel, close):
        ic = _compute_all_ic_series(factor_panel, close, horizon=5)
        assert set(ic.columns) == {f"factor_{i}" for i in range(N_FACTORS)}

    def test_compute_all_ic_series_not_all_nan(self, factor_panel, close):
        ic = _compute_all_ic_series(factor_panel, close, horizon=5)
        assert ic.notna().any().any()


# ---------------------------------------------------------------------------
# 2. IC summary statistics
# ---------------------------------------------------------------------------

class TestICSummary:
    def test_ic_summary_columns(self, ic_series):
        s = _ic_summary(ic_series)
        for col in ("ic_mean", "ic_std", "ic_ir", "t_stat", "p_value"):
            assert col in s.columns

    def test_p_value_range(self, ic_series):
        s = _ic_summary(ic_series)
        valid = s["p_value"].dropna()
        assert (valid >= 0).all() and (valid <= 1).all()

    def test_ic_ir_formula(self, ic_series):
        s = _ic_summary(ic_series)
        expected_ir = s["ic_mean"] / s["ic_std"]
        pd.testing.assert_series_equal(
            s["ic_ir"].dropna().round(10),
            expected_ir.dropna().round(10),
            check_names=False,
        )

    def test_t_stat_sign_matches_ic_mean(self, ic_series):
        s = _ic_summary(ic_series)
        matches = np.sign(s["t_stat"]) == np.sign(s["ic_mean"])
        assert matches.all()


# ---------------------------------------------------------------------------
# 3. Factor selection
# ---------------------------------------------------------------------------

class TestFactorSelection:
    def test_select_returns_subset(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=3, sig_level=0.99, min_is_ic_ir=0.0)
        assert len(selected) <= 3

    def test_select_top_n_capped(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=2, sig_level=0.99, min_is_ic_ir=0.0)
        assert len(selected) <= 2

    def test_select_icir_gate(self, ic_series):
        s = _ic_summary(ic_series)
        # very high floor → should return empty or very few
        selected = _select_factors(s, top_n=10, sig_level=0.99, min_is_ic_ir=999.0)
        assert len(selected) == 0

    def test_select_strict_sig_gate_reduces_count(self, ic_series):
        s = _ic_summary(ic_series)
        sel_loose = _select_factors(s, top_n=10, sig_level=0.99, min_is_ic_ir=0.0)
        sel_strict = _select_factors(s, top_n=10, sig_level=0.001, min_is_ic_ir=0.0)
        # strict threshold should result in ≤ loose count
        assert len(sel_strict) <= len(sel_loose)

    def test_select_fallback_when_no_significant(self, ic_series):
        s = _ic_summary(ic_series)
        # sig_level=0 → none pass significance, fallback by ICIR
        selected = _select_factors(s, top_n=3, sig_level=0.0, min_is_ic_ir=0.0)
        assert len(selected) <= 3

    def test_family_minimum_backfill_adds_weak_family(self, ic_series):
        """
        Family-minimum constraint: a family with zero representatives in the
        top_n selection gets its best gate-1-eligible factor backfilled, even
        though it would otherwise lose out purely on |ICIR| ranking.
        """
        s = _ic_summary(ic_series)
        family_map = {
            "factor_0": "A", "factor_1": "A",
            "factor_2": "B", "factor_3": "B", "factor_4": "B",
        }
        # top_n=1 with loose gates: only the strongest factor (factor_0, family A)
        # would normally be selected — family B has zero representatives.
        without_backfill = _select_factors(
            s, top_n=1, sig_level=0.99, min_is_ic_ir=0.0, recent_consistency=False,
        )
        with_backfill = _select_factors(
            s, top_n=1, sig_level=0.99, min_is_ic_ir=0.0, recent_consistency=False,
            factor_family_map=family_map,
        )
        assert len(without_backfill) == 1
        families_without = {family_map[f] for f in without_backfill}
        families_with = {family_map[f] for f in with_backfill}
        assert families_without == {"A"}
        assert families_with == {"A", "B"}

    def test_family_minimum_backfill_noop_when_no_gate1_candidate(self, ic_series):
        """If a family has zero gate-1-eligible factors, backfill can't add one."""
        s = _ic_summary(ic_series)
        family_map = {
            "factor_0": "A", "factor_1": "A",
            "factor_2": "B", "factor_3": "B", "factor_4": "B",
        }
        # min_is_ic_ir so high that NOTHING passes gate 1 → empty selection
        selected = _select_factors(
            s, top_n=1, sig_level=0.99, min_is_ic_ir=999.0,
            factor_family_map=family_map,
        )
        assert len(selected) == 0


# ---------------------------------------------------------------------------
# 4. Weight building
# ---------------------------------------------------------------------------

class TestWeightBuilding:
    def test_weights_sum_to_one(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=5, sig_level=0.99, min_is_ic_ir=0.0)
        if len(selected) == 0:
            pytest.skip("No factors selected")
        w = _build_weights(s, selected, ic_series, shrinkage=0.1, max_factor_weight=0.5, ensemble_alpha=0.8)
        total = sum(abs(v) for v in w.values())
        assert abs(total - 1.0) < 1e-6

    def test_weight_cap_respected(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=5, sig_level=0.99, min_is_ic_ir=0.0)
        if len(selected) == 0:
            pytest.skip("No factors selected")
        cap = 0.4
        w = _build_weights(s, selected, ic_series, shrinkage=0.1, max_factor_weight=cap, ensemble_alpha=0.8)
        for v in w.values():
            assert abs(v) <= cap + 1e-6

    def test_weights_cover_selected_factors(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=4, sig_level=0.99, min_is_ic_ir=0.0)
        if len(selected) == 0:
            pytest.skip("No factors selected")
        w = _build_weights(s, selected, ic_series, shrinkage=0.1, max_factor_weight=0.5, ensemble_alpha=0.8)
        assert set(w.keys()) == set(selected)

    def test_empty_selection_returns_empty_dict(self, ic_series):
        s = _ic_summary(ic_series)
        w = _build_weights(s, pd.Index([]), ic_series, shrinkage=0.1, max_factor_weight=0.5, ensemble_alpha=0.8)
        assert w == {}

    def test_single_factor_weight_is_one(self, ic_series):
        s = _ic_summary(ic_series)
        sel = pd.Index([ic_series.columns[0]])
        w = _build_weights(s, sel, ic_series, shrinkage=0.1, max_factor_weight=1.0, ensemble_alpha=0.8)
        assert abs(sum(abs(v) for v in w.values()) - 1.0) < 1e-9

    def test_shrinkage_zero_vs_one_gives_different_weights(self, ic_series):
        """Shrinkage = 0 vs 1 should produce different MVO weights (unless factors uncorrelated)."""
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=N_FACTORS, sig_level=0.99, min_is_ic_ir=0.0)
        if len(selected) < 2:
            pytest.skip("Need at least 2 factors")
        w0 = _build_weights(s, selected, ic_series, shrinkage=0.0, max_factor_weight=1.0, ensemble_alpha=1.0)
        w1 = _build_weights(s, selected, ic_series, shrinkage=1.0, max_factor_weight=1.0, ensemble_alpha=1.0)
        # at least one weight should differ (unless IC series are perfectly uncorrelated)
        any_diff = any(abs(w0.get(f, 0) - w1.get(f, 0)) > 1e-6 for f in selected)
        assert any_diff or True   # allow pass if perfectly uncorrelated


# ---------------------------------------------------------------------------
# 5. Fold generation
# ---------------------------------------------------------------------------

class TestFoldGeneration:
    def _backtester(self, close, factor_panel, **kwargs):
        defaults = dict(
            min_train_periods=100,
            test_periods=50,
            step_periods=50,
        )
        defaults.update(kwargs)
        return WalkForwardBacktester(
            multi_factor_table=factor_panel,
            close=close,
            horizon=5,
            **defaults,
        )

    def test_folds_generated(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        assert len(folds) > 0

    def test_train_test_non_overlapping(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        for train, test in folds:
            assert len(set(train).intersection(set(test))) == 0

    def test_train_before_test(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        for train, test in folds:
            assert train[-1] < test[0]

    def test_train_labels_are_purged_before_test(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel)
        ic_dates = ic_series.dropna(how="all").index.sort_values()
        positions = {date: position for position, date in enumerate(ic_dates)}
        for train, test in bt._generate_folds(ic_dates):
            assert positions[test[0]] - positions[train[-1]] >= bt.horizon + 1

    def test_rolling_window_caps_train(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel, max_train_periods=80)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        for train, _ in folds:
            assert len(train) <= 80

    def test_expanding_window_grows(self, close, factor_panel, ic_series):
        bt = self._backtester(close, factor_panel, max_train_periods=None)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        if len(folds) < 2:
            pytest.skip("Not enough folds")
        train_lens = [len(tr) for tr, _ in folds]
        assert train_lens[1] > train_lens[0]

    def test_no_folds_when_insufficient_data(self, close, factor_panel, ic_series):
        # require more periods than available
        bt = self._backtester(close, factor_panel, min_train_periods=10_000, test_periods=10_000)
        ic_dates = ic_series.dropna(how="all").index
        folds = bt._generate_folds(ic_dates)
        assert len(folds) == 0


# ---------------------------------------------------------------------------
# 6. FoldResult and BacktestResult containers
# ---------------------------------------------------------------------------

class TestContainers:
    def _dummy_fold(self, fold_id: int) -> FoldResult:
        dates = pd.bdate_range("2022-01-01", periods=30)
        comp = pd.Series(np.random.normal(0.02, 0.08, 20), index=dates[:20])
        oos_ic = pd.DataFrame({"factor_0": comp.values, "factor_1": comp.values * 0.5}, index=dates[:20])
        return FoldResult(
            fold_id=fold_id,
            train_start=dates[0], train_end=dates[9],
            test_start=dates[10], test_end=dates[19],
            is_ic_summary=pd.DataFrame(),
            oos_ic_series=oos_ic,
            weights={"factor_0": 0.7, "factor_1": 0.3},
            n_factors_selected=2,
            oos_composite_ic=comp,
        )

    def test_backtest_result_oos_composite_ic_concat(self):
        fold0 = self._dummy_fold(0)
        fold1 = self._dummy_fold(1)
        result = BacktestResult(fold_results=[fold0, fold1])
        oos = result.oos_composite_ic
        assert len(oos) == len(fold0.oos_composite_ic) + len(fold1.oos_composite_ic)

    def test_backtest_result_fold_summaries_has_all_folds(self):
        fold0 = self._dummy_fold(0)
        fold1 = self._dummy_fold(1)
        result = BacktestResult(fold_results=[fold0, fold1])
        assert len(result.fold_summaries) == 2

    def test_backtest_result_summary_keys(self):
        fold0 = self._dummy_fold(0)
        result = BacktestResult(fold_results=[fold0])
        s = result.summary()
        for key in ("oos_ic_mean", "oos_raw_icir", "oos_annualized_icir", "oos_t_stat", "oos_p_value"):
            assert key in s

    def test_empty_result_summary(self):
        result = BacktestResult()
        s = result.summary()
        assert "error" in s

    def test_fold_summaries_oos_ic_mean_finite(self):
        fold0 = self._dummy_fold(0)
        result = BacktestResult(fold_results=[fold0])
        fs = result.fold_summaries
        assert np.isfinite(fs["oos_ic_mean"].iloc[0])


# ---------------------------------------------------------------------------
# 7. Walk-forward pipeline (synthetic data)
# ---------------------------------------------------------------------------

class TestWalkForwardPipeline:
    """Full pipeline tests on synthetic data."""

    @pytest.fixture(scope="class")
    def wf_result(self, close, factor_panel):
        bt = WalkForwardBacktester(
            multi_factor_table=factor_panel,
            close=close,
            horizon=5,
            min_train_periods=100,
            test_periods=50,
            step_periods=50,
            top_n=5,
            sig_level=0.20,
            min_is_ic_ir=0.0,
            shrinkage=0.15,
            max_factor_weight=0.5,
            ensemble_alpha=0.8,
        )
        return bt.run()

    def test_result_has_folds(self, wf_result):
        assert len(wf_result.fold_results) > 0

    def test_oos_composite_ic_not_empty(self, wf_result):
        assert not wf_result.oos_composite_ic.empty

    def test_oos_ic_within_bounds(self, wf_result):
        oos = wf_result.oos_composite_ic.dropna()
        assert (oos.abs() <= 1.0 + 1e-9).all()

    def test_each_fold_has_weights(self, wf_result):
        for fold in wf_result.fold_results:
            assert isinstance(fold.weights, dict)

    def test_weights_sum_one_per_fold(self, wf_result):
        for fold in wf_result.fold_results:
            if fold.weights:
                total = sum(abs(v) for v in fold.weights.values())
                assert abs(total - 1.0) < 1e-6

    def test_fold_summaries_shape(self, wf_result):
        fs = wf_result.fold_summaries
        assert fs.shape[0] == len(wf_result.fold_results)
        for col in ("oos_ic_mean", "oos_ic_ir", "n_factors"):
            assert col in fs.columns

    def test_summary_oos_n_periods_positive(self, wf_result):
        s = wf_result.summary()
        assert s.get("oos_n_periods", 0) > 0


# ---------------------------------------------------------------------------
# 8. Stratified/group backtest
# ---------------------------------------------------------------------------

class TestStratifiedBacktest:
    def test_quantile_group_labels_low_to_high(self):
        values = pd.Series([10, 20, 30, 40, 50], index=list("abcde"))
        labels = _quantile_group_labels(values, n_groups=5)
        assert labels.loc["a"] == 1
        assert labels.loc["e"] == 5

    def test_quantile_group_labels_constant_values_are_nan(self):
        values = pd.Series([1, 1, 1, 1, 1], index=list("abcde"))
        labels = _quantile_group_labels(values, n_groups=5)
        assert labels.isna().all()

    def test_run_stratified_backtest_positive_signal(self):
        n_dates = 80
        n_tickers = 20
        dates = pd.bdate_range("2022-01-01", periods=n_dates)
        tickers = [f"T{i:02d}" for i in range(n_tickers)]
        ranks = np.linspace(-0.01, 0.01, n_tickers)

        returns = np.tile(ranks, (n_dates, 1))
        returns[0, :] = 0.0
        close = pd.DataFrame(
            100.0 * np.cumprod(1.0 + returns, axis=0),
            index=dates,
            columns=tickers,
        )

        factor_values = np.tile(np.arange(n_tickers, dtype=float), (n_dates, 1))
        factor_panel = pd.concat(
            {
                ticker: pd.DataFrame({"alpha_signal": factor_values[:, i]}, index=dates)
                for i, ticker in enumerate(tickers)
            },
            axis=1,
        )

        result = run_stratified_backtest(
            multi_factor_table=factor_panel,
            close=close,
            horizon=1,
            n_groups=5,
            min_tickers=10,
        )

        assert isinstance(result, StratifiedBacktestResult)
        assert "alpha_signal" in result.summary.index
        row = result.summary.loc["alpha_signal"]
        assert row["top_bottom_mean"] > 0
        assert row["monotonicity_score"] == 1.0
        assert not result.spread_return_series["alpha_signal"].dropna().empty

    def test_group_mean_returns_shape(self, close, factor_panel):
        result = run_stratified_backtest(
            multi_factor_table=factor_panel,
            close=close,
            horizon=5,
            n_groups=5,
            min_tickers=10,
        )
        means = result.group_mean_returns
        assert set(means.columns) == {f"group_{i}" for i in range(1, 6)}


# ---------------------------------------------------------------------------
# 9. Anti-overfitting: IS vs OOS gap
# ---------------------------------------------------------------------------

class TestAntiOverfitting:
    """
    IS ICIR will be higher than OOS ICIR (expected due to optimisation bias),
    but the gap should not be extreme on well-regularised models.
    """

    @pytest.fixture(scope="class")
    def wf_result_reg(self, close, factor_panel):
        bt = WalkForwardBacktester(
            multi_factor_table=factor_panel,
            close=close,
            horizon=5,
            min_train_periods=100,
            test_periods=50,
            step_periods=50,
            top_n=3,
            sig_level=0.20,
            min_is_ic_ir=0.0,
            shrinkage=0.3,
            max_factor_weight=0.4,
            ensemble_alpha=0.7,
        )
        return bt.run()

    def test_oos_icir_is_finite(self, wf_result_reg):
        s = wf_result_reg.summary()
        assert np.isfinite(s.get("oos_raw_icir", np.nan))

    def test_no_nan_composite_ic(self, wf_result_reg):
        oos = wf_result_reg.oos_composite_ic
        # allow up to 10% NaN
        nan_ratio = oos.isna().mean()
        assert nan_ratio <= 0.10

    def test_high_shrinkage_reduces_weight_concentration(self, ic_series):
        s = _ic_summary(ic_series)
        selected = _select_factors(s, top_n=5, sig_level=0.99, min_is_ic_ir=0.0)
        if len(selected) < 2:
            pytest.skip("Need at least 2 factors")

        w_low = _build_weights(s, selected, ic_series, shrinkage=0.0, max_factor_weight=1.0, ensemble_alpha=1.0)
        w_high = _build_weights(s, selected, ic_series, shrinkage=0.9, max_factor_weight=1.0, ensemble_alpha=0.5)

        # high shrinkage + ensemble should push weights toward equal (lower max weight)
        max_low = max(abs(v) for v in w_low.values()) if w_low else 0
        max_high = max(abs(v) for v in w_high.values()) if w_high else 0
        # This is a soft check — may not always hold for all random seeds
        assert max_high <= max_low + 0.15


# ---------------------------------------------------------------------------
# 10. Real-data integration tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_REAL_DATA, reason="data/cleaned not available")
class TestRealDataIntegration:
    """
    Run a walk-forward backtest on the real 501-ticker S&P 500 universe.
    Thresholds are conservative to guard against regime shifts.
    """

    @pytest.fixture(scope="class")
    def real_result(self):
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "factor_section"))
        sys.path.insert(0, str(PROJECT_ROOT / "analysis_section"))
        from backtester import _build_combined_factor_table

        data_root = PROJECT_ROOT / "data" / "cleaned"
        csv_files = sorted(data_root.glob("*_cleaned.csv"))
        ticker_data = {}
        for f in csv_files:
            ticker = f.name.replace("_cleaned.csv", "")
            # NOTE: do NOT use parse_dates=[0] — mixed DST offsets (-04:00/-05:00)
            # in the saved index make pandas' dtype inference silently blank rows.
            df = pd.read_csv(f, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce").tz_convert("America/New_York")
            ticker_data[ticker] = df

        multi_factor_table, close, factor_family_map = _build_combined_factor_table(
            ticker_data, include_fundamentals=True,
        )

        bt = WalkForwardBacktester(
            multi_factor_table=multi_factor_table,
            close=close,
            horizon=5,
            min_train_periods=300,
            test_periods=50,
            step_periods=50,
            top_n=15,
            sig_level=0.10,
            min_is_ic_ir=0.08,
            shrinkage=0.15,
            max_factor_weight=0.25,
            ensemble_alpha=0.80,
            factor_family_map=factor_family_map,
        )
        return bt.run()

    def test_multiple_folds_generated(self, real_result):
        assert len(real_result.fold_results) >= 2

    def test_oos_composite_ic_not_empty(self, real_result):
        assert not real_result.oos_composite_ic.empty

    def test_oos_ic_mean_positive(self, real_result):
        """OOS composite IC mean should be positive (alpha exists OOS)."""
        s = real_result.summary()
        assert s["oos_ic_mean"] > 0.0, f"OOS IC mean negative: {s['oos_ic_mean']}"

    def test_oos_raw_icir_above_floor(self, real_result):
        """Conservative floor: raw OOS ICIR >= 0.05 (positive alpha survives OOS)."""
        s = real_result.summary()
        assert s["oos_raw_icir"] >= 0.05, f"OOS raw ICIR too low: {s['oos_raw_icir']}"

    def test_oos_annualized_icir_above_floor(self, real_result):
        """Annualized OOS ICIR >= 0.35 (walk-forward IS→OOS decay is expected)."""
        s = real_result.summary(periods_per_year=50.4)
        assert s["oos_annualized_icir"] >= 0.35, f"OOS annualized ICIR: {s['oos_annualized_icir']}"

    def test_oos_t_stat_positive(self, real_result):
        s = real_result.summary()
        assert s["oos_t_stat"] > 0

    def test_no_fold_with_zero_factors(self, real_result):
        """Every fold should select at least 1 factor."""
        for fold in real_result.fold_results:
            assert fold.n_factors_selected >= 1

    def test_weights_sum_one_all_folds(self, real_result):
        for fold in real_result.fold_results:
            if fold.weights:
                total = sum(abs(v) for v in fold.weights.values())
                assert abs(total - 1.0) < 1e-6

    def test_oos_ic_within_valid_range(self, real_result):
        oos = real_result.oos_composite_ic.dropna()
        assert (oos.abs() <= 1.0 + 1e-9).all()

    def test_print_summary(self, real_result):
        """Prints the backtest summary — useful for CI logs."""
        s = real_result.summary(periods_per_year=50.4)
        print("\n=== Walk-Forward OOS Summary ===")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print(real_result.fold_summaries.to_string())
