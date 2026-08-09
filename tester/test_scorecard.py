# tester/test_scorecard.py
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from research_platform.scorecard import calmar_ratio, sortino_ratio, win_rate


def test_win_rate_is_fraction_of_positive_periods():
    net = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01])
    assert win_rate(net) == pytest.approx(3 / 5)


def test_sortino_uses_downside_deviation_only():
    net = pd.Series([0.01, -0.02, 0.01, -0.01, 0.02])
    downside = pd.Series([min(x, 0.0) for x in net]).std(ddof=1)
    expected = (net.mean() * 252) / (downside * np.sqrt(252))
    assert sortino_ratio(net, periods_per_year=252) == pytest.approx(expected)


def test_sortino_is_nan_without_downside():
    net = pd.Series([0.01, 0.02, 0.03])
    assert np.isnan(sortino_ratio(net))


def test_calmar_is_annual_return_over_abs_drawdown():
    assert calmar_ratio(annualized_return=0.12, max_drawdown=-0.10) == pytest.approx(1.2)
    assert np.isnan(calmar_ratio(0.12, 0.0))


# append to tester/test_scorecard.py
from research_platform.scorecard import build_factor_scorecard


def _monotone_factor_inputs():
    dates = pd.bdate_range("2021-01-01", periods=120)
    tickers = [f"T{i}" for i in range(40)]
    rng = np.random.default_rng(0)
    base = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))), index=dates, columns=tickers)
    # forward return increases with the factor rank -> positive IC, monotone groups
    forward = base.rank(axis=1) / len(tickers) * 0.02 + rng.standard_normal((len(dates), len(tickers))) * 0.001
    return {"good": base}, forward


def test_factor_scorecard_has_expected_columns_and_positive_ic():
    factors, forward = _monotone_factor_inputs()
    table = build_factor_scorecard(
        factors, forward, forward.index, n_groups=5, min_names=10, horizon=5
    ).set_index("factor")
    for col in ["rank_ic", "rank_ic_t", "pearson_ic", "icir", "icir_annualized",
                "ic_hit_rate", "monotonicity", "long_short_t", "rank_turnover",
                "coverage", "n_obs"]:
        assert col in table.columns
    assert table.loc["good", "rank_ic"] > 0
    assert table.loc["good", "monotonicity"] > 0.5
    assert 0.0 <= table.loc["good", "ic_hit_rate"] <= 1.0


# append to tester/test_scorecard.py
from research_platform.scorecard import build_portfolio_scorecard


class _FakePortfolio:
    def __init__(self, net, weights):
        self.net_returns = net
        self.weights = weights
        clean = net.dropna()
        ann = float(clean.mean() * 252)
        vol = float(clean.std(ddof=1) * np.sqrt(252))
        curve = (1 + clean).cumprod()
        dd = float((curve / curve.cummax() - 1).min())
        self.metrics = {
            "annualized_return": ann,
            "annualized_volatility": vol,
            "sharpe": ann / vol if vol else np.nan,
            "max_drawdown": dd,
            "average_turnover": 0.1,
            "total_cost": 0.02,
        }


class _FakeExperiment:
    def __init__(self, scores, portfolios):
        self.scores = scores
        self.portfolios = portfolios


def test_portfolio_scorecard_adds_sortino_calmar_winrate():
    dates = pd.bdate_range("2021-01-01", periods=80)
    tickers = ["A", "B", "C"]
    rng = np.random.default_rng(1)
    score = pd.DataFrame(rng.standard_normal((len(dates), 3)), index=dates, columns=tickers)
    forward = pd.DataFrame(rng.standard_normal((len(dates), 3)) * 0.01, index=dates, columns=tickers)
    weights = pd.DataFrame(1 / 3, index=dates, columns=tickers)
    net = pd.Series(rng.standard_normal(len(dates)) * 0.01 + 0.0005, index=dates)
    experiment = _FakeExperiment({"raw": score}, {"raw": _FakePortfolio(net, weights)})
    industry = pd.DataFrame("Tech", index=dates, columns=tickers)
    table = build_portfolio_scorecard(
        experiment, forward, industry, min_names=2, periods_per_year=252
    ).set_index("path")
    for col in ["annualized_return", "sharpe", "sortino", "max_drawdown", "calmar",
                "win_rate", "average_turnover", "total_cost", "regime_consistency",
                "industry_exposure"]:
        assert col in table.columns
    assert 0.0 <= table.loc["raw", "win_rate"] <= 1.0


# append to tester/test_scorecard.py
from research_platform.scorecard import build_group_backtest


def test_group_backtest_is_monotone_for_a_monotone_signal():
    factors, forward = _monotone_factor_inputs()
    table = build_group_backtest(
        factors, forward, n_groups=5, min_names=10, horizon=5, periods_per_year=252
    )
    good = table[table["series"] == "good"].set_index("group")
    for col in ["mean_forward_return", "annualized_return", "sharpe", "cumulative_return", "avg_count"]:
        assert col in table.columns
    # top group out-returns bottom group
    assert good.loc["group_5", "annualized_return"] > good.loc["group_1", "annualized_return"]


# append to tester/test_scorecard.py
from research_platform.scorecard import build_correlation_views


def test_correlation_views_return_square_matrix_and_clusters():
    dates = pd.bdate_range("2021-01-01", periods=120)
    tickers = [f"T{i}" for i in range(40)]
    rng = np.random.default_rng(2)
    a = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))), index=dates, columns=tickers)
    b = a + rng.standard_normal((len(dates), len(tickers))) * 0.01  # near-duplicate of a
    c = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))), index=dates, columns=tickers)
    forward = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))) * 0.01, index=dates, columns=tickers)
    views = build_correlation_views({"a": a, "b": b, "c": c}, forward, dates, min_names=10, min_pair_dates=20)
    value_matrix = views["value_matrix"]
    assert list(value_matrix.index) == list(value_matrix.columns)  # square
    assert value_matrix.loc["a", "a"] == pytest.approx(1.0, abs=1e-9)
    assert value_matrix.loc["a", "b"] > 0.9  # near-duplicates highly correlated
    assert set(views["clusters"].columns) >= {"cluster", "members"}


# append to tester/test_scorecard.py
from research_platform.scorecard import render_scorecard_markdown, write_scorecard


def test_render_and_write_scorecard(tmp_path):
    factor_tbl = pd.DataFrame({"factor": ["x"], "rank_ic": [0.0312345], "n_obs": [100]})
    corr = pd.DataFrame([[1.0, 0.6123], [0.6123, 1.0]], index=["x", "y"], columns=["x", "y"])
    tables = {
        "factor_scorecard": factor_tbl,
        "value_matrix": corr,
    }
    markdown = render_scorecard_markdown(tables, matrix_tables=("value_matrix",))
    assert "no verdict" in markdown.lower() or "无判决" in markdown
    assert "0.0312" in markdown  # rounded
    # matrix keeps row index label; tabulate pads single-char index cells to
    # the separator's minimum width (":---" needs >= 3 dashes), so the exact
    # rendering is "| x  |" rather than "| x |" -- match loosely on whitespace.
    assert re.search(r"\|\s*x\s*\|", markdown)
    paths = write_scorecard(tables, markdown, tmp_path)
    assert (tmp_path / "scorecard.md").exists()
    assert (tmp_path / "factor_scorecard.csv").exists()
    assert (tmp_path / "value_matrix.csv").exists()
    # deterministic: same inputs -> identical bytes
    md2 = render_scorecard_markdown(tables, matrix_tables=("value_matrix",))
    assert md2 == markdown


# append to tester/test_scorecard.py -- degenerate-input coverage (spec §7)
def _degenerate_factor_inputs():
    dates = pd.bdate_range("2021-01-01", periods=60)
    tickers = [f"T{i}" for i in range(20)]
    rng = np.random.default_rng(3)
    good = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))), index=dates, columns=tickers)
    nan_panel = pd.DataFrame(np.nan, index=dates, columns=tickers)
    forward = pd.DataFrame(rng.standard_normal((len(dates), len(tickers))) * 0.01, index=dates, columns=tickers)
    return dates, good, nan_panel, forward


def test_factor_scorecard_degenerate_inputs_do_not_crash():
    dates, good, nan_panel, forward = _degenerate_factor_inputs()
    # all-NaN factor panel -> no date ever has enough non-NaN names
    all_nan = build_factor_scorecard(
        {"nanfac": nan_panel}, forward, dates, n_groups=5, min_names=10, horizon=5
    ).set_index("factor")
    assert len(all_nan) == 1
    assert np.isnan(all_nan.loc["nanfac", "rank_ic"])
    assert all_nan.loc["nanfac", "n_obs"] == 0

    # min_names larger than the whole cross-section -> every date fails the filter
    starved = build_factor_scorecard(
        {"good": good}, forward, dates, n_groups=5, min_names=1000, horizon=5
    ).set_index("factor")
    assert len(starved) == 1
    assert np.isnan(starved.loc["good", "rank_ic"])
    assert starved.loc["good", "n_obs"] == 0


def test_portfolio_scorecard_degenerate_inputs_do_not_crash():
    dates = pd.bdate_range("2021-01-01", periods=60)
    tickers = ["A", "B", "C"]
    rng = np.random.default_rng(4)
    forward = pd.DataFrame(rng.standard_normal((len(dates), 3)) * 0.01, index=dates, columns=tickers)
    industry = pd.DataFrame("Tech", index=dates, columns=tickers)
    weights = pd.DataFrame(1 / 3, index=dates, columns=tickers)

    # all-NaN score and all-NaN net returns
    nan_score = pd.DataFrame(np.nan, index=dates, columns=tickers)
    nan_net = pd.Series(np.nan, index=dates)
    experiment = _FakeExperiment({"raw": nan_score}, {"raw": _FakePortfolio(nan_net, weights)})
    all_nan = build_portfolio_scorecard(experiment, forward, industry, min_names=2).set_index("path")
    assert len(all_nan) == 1
    assert np.isnan(all_nan.loc["raw", "sortino"])
    assert np.isnan(all_nan.loc["raw", "win_rate"])
    assert all_nan.loc["raw", "n_subperiods"] == 0

    # min_names larger than the whole cross-section -> IC/regime series go empty
    score = pd.DataFrame(rng.standard_normal((len(dates), 3)), index=dates, columns=tickers)
    net = pd.Series(rng.standard_normal(len(dates)) * 0.01, index=dates)
    experiment2 = _FakeExperiment({"raw": score}, {"raw": _FakePortfolio(net, weights)})
    starved = build_portfolio_scorecard(experiment2, forward, industry, min_names=1000).set_index("path")
    assert len(starved) == 1
    assert starved.loc["raw", "n_subperiods"] == 0
    # portfolio-level metrics (not IC-derived) still come through untouched
    assert np.isfinite(starved.loc["raw", "annualized_return"])


def test_group_backtest_degenerate_inputs_do_not_crash():
    dates, good, nan_panel, forward = _degenerate_factor_inputs()

    all_nan = build_group_backtest({"nanfac": nan_panel}, forward, n_groups=5, min_names=10, horizon=5)
    assert len(all_nan) == 5  # one row per group, even with no observations
    assert all_nan["annualized_return"].isna().all()
    assert all_nan["cumulative_return"].isna().all()

    starved = build_group_backtest({"good": good}, forward, n_groups=5, min_names=1000, horizon=5)
    assert len(starved) == 5
    assert starved["annualized_return"].isna().all()
    assert starved["cumulative_return"].isna().all()


def test_correlation_views_degenerate_inputs_do_not_crash():
    dates, good, nan_panel, forward = _degenerate_factor_inputs()

    views = build_correlation_views(
        {"good": good, "nanfac": nan_panel}, forward, dates, min_names=10, min_pair_dates=20
    )
    value_matrix = views["value_matrix"]
    assert list(value_matrix.index) == list(value_matrix.columns)  # still square
    assert value_matrix.loc["good", "good"] == pytest.approx(1.0, abs=1e-9)
    assert np.isnan(value_matrix.loc["nanfac", "good"])
    assert {"cluster", "members"} <= set(views["clusters"].columns)

    starved = build_correlation_views(
        {"a": good, "b": good.copy()}, forward, dates, min_names=1000, min_pair_dates=20
    )
    starved_matrix = starved["value_matrix"]
    assert list(starved_matrix.index) == list(starved_matrix.columns)
    assert starved_matrix.isna().all().all()  # nothing ever cleared min_names
