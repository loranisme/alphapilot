# 因子验证记分卡 (scorecard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure summarization layer that rolls the platform's scattered factor-validation evidence into human-readable scorecard tables (per-factor metrics, per-portfolio metrics, group backtest, correlation matrices) written primarily as a readable `scorecard.md`.

**Architecture:** New `research_platform/scorecard.py` holds pure functions that consume already-computed factor panels and the OOS experiment result and return `DataFrame`s plus a markdown string. It reuses `evaluation`, `portfolio`, `correlation`, `regime`, and `reporting._atomic_text`. No verdict/gate logic — metrics only. Wired into `run_oos_alpha_validation`.

**Tech Stack:** Python 3.13, pandas, numpy, pytest. Follows existing platform conventions (frozen behavior, atomic writes, `to_markdown` for human tables).

---

## File Structure

- Create `research_platform/scorecard.py` — all builders + renderer + writer (one responsibility: summarize evidence for humans).
- Create `tester/test_scorecard.py` — unit tests for every metric and builder.
- Modify `scripts/run_oos_alpha_validation.py` — call the builders and write the scorecard alongside the existing OOS report.
- Modify `tester/test_oos_real.py` — assert the scorecard artifacts exist and reproduce.

Existing APIs reused (verified):
- `evaluation.evaluate_ic(factor, forward_returns, method="spearman", min_names=30) -> pd.Series`
- `evaluation.run_quantile_backtest(factor, forward_returns, n_groups=5, min_names=30) -> QuantileBacktestResult(group_returns, group_counts, spread)`
- `evaluation.group_stratification_table(scores: dict, forward_returns, n_groups=5, min_names=30) -> DataFrame[path, group_*, monotonicity, top_bottom_mean, top_bottom_t, n_obs]`
- `portfolio.PortfolioResult(weights, gross_returns, net_returns, turnover, metrics)`; `metrics` keys: `annualized_return, annualized_volatility, sharpe, max_drawdown, average_turnover, total_cost`
- `correlation.factor_value_correlation(factors, dates, directions, min_names=30, min_dates=60) -> CorrelationEstimate(values, verified, counts)`
- `correlation.ic_correlation(ic_frame, directions, shrinkage=0.5) -> CorrelationEstimate`
- `correlation.connected_correlation_clusters(values, verified, threshold=0.75) -> DataFrame[factor, cluster, ...]`
- `correlation.factor_rank_turnover(panel, dates, rebalance_interval, min_names) -> float`
- `regime.calendar_year_labels`, `regime.group_daily_ic`, `regime.ic_stability_summary`
- `reporting.industry_exposure_table(weights, industry) -> DataFrame` (has `max_abs_industry` column)
- `reporting._atomic_text(path, content)`

---

## Task 1: Pure ratio metrics (sortino, calmar, win_rate)

**Files:**
- Create: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing tests**

```python
# tester/test_scorecard.py
from __future__ import annotations

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_platform.scorecard'`

- [ ] **Step 3: Write minimal implementation**

```python
# research_platform/scorecard.py
"""Human-readable factor-validation scorecard (pure summarization, no verdicts).

Rolls the platform's already-computed evidence — per-date IC, quantile backtests,
portfolio results, correlations, regime slices — into per-factor and per-portfolio
metric tables, a group backtest, and correlation matrices, plus a readable
``scorecard.md``. This layer deliberately renders NO pass/fail judgement: it only
computes and presents metrics so the researcher can judge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def win_rate(net_returns: pd.Series) -> float:
    """Fraction of periods with a strictly positive net return."""
    clean = pd.to_numeric(net_returns, errors="coerce").dropna()
    return float((clean > 0).mean()) if len(clean) else np.nan


def sortino_ratio(net_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized return over annualized downside deviation (NaN if no downside)."""
    clean = pd.to_numeric(net_returns, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    downside = clean.clip(upper=0.0)
    downside_dev = float(downside.std(ddof=1))
    if not downside_dev > 0:
        return np.nan
    annual_return = float(clean.mean() * periods_per_year)
    return annual_return / (downside_dev * np.sqrt(periods_per_year))


def calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """Annualized return divided by the magnitude of max drawdown (NaN if flat)."""
    if not max_drawdown or not np.isfinite(max_drawdown) or max_drawdown == 0:
        return np.nan
    return float(annualized_return) / abs(float(max_drawdown))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): pure sortino/calmar/win_rate ratio metrics"
```

---

## Task 2: Per-factor scorecard table

**Files:**
- Modify: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_scorecard.py::test_factor_scorecard_has_expected_columns_and_positive_ic -q`
Expected: FAIL with `ImportError: cannot import name 'build_factor_scorecard'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to research_platform/scorecard.py
from .correlation import factor_rank_turnover
from .evaluation import evaluate_ic, group_stratification_table


def _ic_stats(ic: pd.Series) -> dict:
    clean = pd.to_numeric(ic, errors="coerce").dropna()
    n = int(clean.count())
    mean = float(clean.mean()) if n else np.nan
    std = float(clean.std(ddof=1)) if n >= 2 else np.nan
    t = mean / (std / np.sqrt(n)) if std and std > 0 and n else np.nan
    icir = mean / std if std and std > 0 else np.nan
    hit = float((np.sign(clean) == np.sign(mean)).mean()) if n else np.nan
    return {"mean": mean, "std": std, "t": t, "n": n, "icir": icir, "hit": hit}


def build_factor_scorecard(
    factors: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    dates: pd.Index,
    n_groups: int = 5,
    min_names: int = 30,
    horizon: int = 5,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """One row per single factor: IC family, monotonicity, long-short, turnover."""
    strat = group_stratification_table(
        factors, forward_returns, n_groups=n_groups, min_names=min_names
    ).set_index("path")
    rows = []
    for name, panel in factors.items():
        spear = _ic_stats(evaluate_ic(panel, forward_returns, method="spearman", min_names=min_names))
        pear = _ic_stats(evaluate_ic(panel, forward_returns, method="pearson", min_names=min_names))
        direction = 1 if not np.isfinite(spear["mean"]) or spear["mean"] >= 0 else -1
        turnover = factor_rank_turnover(
            panel * direction, dates, rebalance_interval=horizon, min_names=min_names
        )
        coverage = float(panel.reindex(dates).notna().mean().mean())
        rows.append(
            {
                "factor": name,
                "rank_ic": spear["mean"],
                "rank_ic_t": spear["t"],
                "pearson_ic": pear["mean"],
                "icir": spear["icir"],
                "icir_annualized": spear["icir"] * np.sqrt(periods_per_year / horizon)
                if np.isfinite(spear["icir"])
                else np.nan,
                "ic_hit_rate": spear["hit"],
                "monotonicity": float(strat.loc[name, "monotonicity"]) if name in strat.index else np.nan,
                "long_short_mean": float(strat.loc[name, "top_bottom_mean"]) if name in strat.index else np.nan,
                "long_short_t": float(strat.loc[name, "top_bottom_t"]) if name in strat.index else np.nan,
                "rank_turnover": turnover,
                "coverage": coverage,
                "n_obs": spear["n"],
            }
        )
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): per-factor metrics table"
```

---

## Task 3: Per-portfolio scorecard table

**Files:**
- Modify: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_scorecard.py::test_portfolio_scorecard_adds_sortino_calmar_winrate -q`
Expected: FAIL with `ImportError: cannot import name 'build_portfolio_scorecard'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to research_platform/scorecard.py
from .regime import calendar_year_labels, group_daily_ic, ic_stability_summary
from .reporting import industry_exposure_table


def build_portfolio_scorecard(
    experiment,
    forward_returns: pd.DataFrame,
    industry: pd.DataFrame,
    min_names: int = 30,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """One row per portfolio path: performance, risk-adjusted, regime, exposure."""
    rows = []
    for path, portfolio in experiment.portfolios.items():
        metrics = dict(portfolio.metrics)
        net = portfolio.net_returns
        ic = evaluate_ic(experiment.scores[path], forward_returns, min_names=min_names)
        labels = calendar_year_labels(pd.DatetimeIndex(experiment.scores[path].index))
        summary = ic_stability_summary(group_daily_ic(ic, labels))
        exposure = industry_exposure_table(portfolio.weights, industry)
        rows.append(
            {
                "path": path,
                "annualized_return": metrics.get("annualized_return", np.nan),
                "annualized_volatility": metrics.get("annualized_volatility", np.nan),
                "sharpe": metrics.get("sharpe", np.nan),
                "sortino": sortino_ratio(net, periods_per_year),
                "max_drawdown": metrics.get("max_drawdown", np.nan),
                "calmar": calmar_ratio(
                    metrics.get("annualized_return", np.nan), metrics.get("max_drawdown", np.nan)
                ),
                "win_rate": win_rate(net),
                "average_turnover": metrics.get("average_turnover", np.nan),
                "total_cost": metrics.get("total_cost", np.nan),
                "regime_consistency": summary["sign_consistency"],
                "n_subperiods": summary["n_subperiods"],
                "industry_exposure": float(exposure["max_abs_industry"].mean())
                if "max_abs_industry" in exposure.columns and len(exposure)
                else np.nan,
            }
        )
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): per-portfolio metrics with sortino/calmar/win-rate/regime"
```

---

## Task 4: Group backtest table (per series × group)

**Files:**
- Modify: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_scorecard.py::test_group_backtest_is_monotone_for_a_monotone_signal -q`
Expected: FAIL with `ImportError: cannot import name 'build_group_backtest'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to research_platform/scorecard.py
from .evaluation import run_quantile_backtest


def build_group_backtest(
    series: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    n_groups: int = 5,
    min_names: int = 30,
    horizon: int = 5,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """Per series x quantile group: annualized/sharpe on overlapping h-period
    forward returns, plus a non-overlapping compounded cumulative return.

    Group returns are daily-sampled h-period forward returns, so annualization
    uses ``periods_per_year / horizon`` and cumulative compounding uses a
    non-overlapping ``::horizon`` subsample to avoid double counting.
    """
    scale = np.sqrt(periods_per_year / horizon)
    rows = []
    for name, panel in series.items():
        result = run_quantile_backtest(panel, forward_returns, n_groups=n_groups, min_names=min_names)
        for col in result.group_returns.columns:
            block = pd.to_numeric(result.group_returns[col], errors="coerce").dropna()
            mean = float(block.mean()) if len(block) else np.nan
            std = float(block.std(ddof=1)) if len(block) >= 2 else np.nan
            nonoverlap = block.iloc[::horizon]
            rows.append(
                {
                    "series": name,
                    "group": col,
                    "mean_forward_return": mean,
                    "annualized_return": mean * periods_per_year / horizon
                    if np.isfinite(mean)
                    else np.nan,
                    "sharpe": (mean / std) * scale if std and std > 0 else np.nan,
                    "cumulative_return": float((1 + nonoverlap).prod() - 1)
                    if len(nonoverlap)
                    else np.nan,
                    "avg_count": float(result.group_counts[col].mean())
                    if col in result.group_counts.columns
                    else np.nan,
                }
            )
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): per-group backtest aggregation"
```

---

## Task 5: Correlation matrices + clusters

**Files:**
- Modify: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_scorecard.py::test_correlation_views_return_square_matrix_and_clusters -q`
Expected: FAIL with `ImportError: cannot import name 'build_correlation_views'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to research_platform/scorecard.py
from .correlation import (
    connected_correlation_clusters,
    factor_value_correlation,
    ic_correlation,
)


def build_correlation_views(
    factors: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    dates: pd.Index,
    min_names: int = 30,
    min_pair_dates: int = 60,
    hard_threshold: float = 0.75,
    ic_shrinkage: float = 0.5,
) -> dict[str, pd.DataFrame]:
    """Square value-correlation and IC-correlation matrices plus readable clusters."""
    directions = {}
    ic_columns = {}
    for name, panel in factors.items():
        ic = evaluate_ic(panel, forward_returns, method="spearman", min_names=min_names)
        ic_columns[name] = ic
        mean = float(pd.to_numeric(ic, errors="coerce").mean())
        directions[name] = 1 if not np.isfinite(mean) or mean >= 0 else -1
    value_estimate = factor_value_correlation(
        factors, dates, directions, min_names=min_names, min_dates=min_pair_dates
    )
    ic_frame = pd.DataFrame(ic_columns).reindex(index=pd.Index(dates))
    ic_estimate = ic_correlation(ic_frame, directions, shrinkage=ic_shrinkage)
    cluster_rows = connected_correlation_clusters(
        value_estimate.values, value_estimate.verified, threshold=hard_threshold
    )
    clusters = (
        cluster_rows.groupby("cluster")["factor"]
        .apply(lambda names: " | ".join(sorted(names)))
        .reset_index()
        .rename(columns={"factor": "members"})
    )
    clusters["cluster_size"] = clusters["members"].str.split(" | ", regex=False).apply(len)
    return {
        "value_matrix": value_estimate.values.round(4),
        "ic_matrix": ic_estimate.values.round(4),
        "clusters": clusters,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): square correlation matrices and readable clusters"
```

---

## Task 6: Markdown renderer + atomic writer

**Files:**
- Modify: `research_platform/scorecard.py`
- Test: `tester/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
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
    assert "| x |" in markdown  # matrix keeps row index label
    paths = write_scorecard(tables, markdown, tmp_path)
    assert (tmp_path / "scorecard.md").exists()
    assert (tmp_path / "factor_scorecard.csv").exists()
    assert (tmp_path / "value_matrix.csv").exists()
    # deterministic: same inputs -> identical bytes
    md2 = render_scorecard_markdown(tables, matrix_tables=("value_matrix",))
    assert md2 == markdown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_scorecard.py::test_render_and_write_scorecard -q`
Expected: FAIL with `ImportError: cannot import name 'render_scorecard_markdown'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to research_platform/scorecard.py
from pathlib import Path

from .reporting import _atomic_text

_TITLES = {
    "factor_scorecard": "板块① 因子层",
    "portfolio_scorecard": "板块② 组合层",
    "group_backtest": "分组回测",
    "value_matrix": "相关性矩阵 · 因子值",
    "ic_matrix": "相关性矩阵 · IC",
    "clusters": "相关性聚类 (@0.75)",
}


def render_scorecard_markdown(
    tables: dict[str, pd.DataFrame],
    matrix_tables: tuple[str, ...] = ("value_matrix", "ic_matrix"),
    round_to: int = 4,
) -> str:
    """Render tables as readable markdown; matrices keep their row index."""
    lines = [
        "# 因子验证记分卡",
        "",
        "> 纯指标汇总，**无判决 (no verdict)**；阈值判断由使用者依据下列指标自行下。",
        "",
    ]
    for name, table in tables.items():
        title = _TITLES.get(name, name)
        lines.extend([f"## {title}", ""])
        if table is None or table.empty:
            lines.extend(["_无数据_", ""])
            continue
        keep_index = name in matrix_tables
        rounded = table.round(round_to)
        lines.append(rounded.to_markdown(index=keep_index))
        lines.append("")
    return "\n".join(lines) + "\n"


def write_scorecard(
    tables: dict[str, pd.DataFrame],
    markdown: str,
    output_dir: str | Path,
    matrix_tables: tuple[str, ...] = ("value_matrix", "ic_matrix"),
) -> list[Path]:
    """Write scorecard.md (primary) then a CSV copy of each table (deterministic)."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "scorecard.md"
    _atomic_text(report_path, markdown)
    paths = [report_path]
    for name in sorted(tables):
        table = tables[name]
        if table is None:
            continue
        csv_path = output / f"{name}.csv"
        _atomic_text(csv_path, table.to_csv(index=name in matrix_tables))
        paths.append(csv_path)
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/scorecard.py tester/test_scorecard.py
git commit -m "feat(scorecard): readable markdown renderer and atomic writer"
```

---

## Task 7: Wire scorecard into the OOS runner

**Files:**
- Modify: `scripts/run_oos_alpha_validation.py`
- Modify: `tester/test_oos_real.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tester/test_oos_real.py — reuse the file's existing HAS_REAL_DATA guard
@pytest.mark.skipif(not HAS_REAL_DATA, reason="real factor universe is unavailable")
def test_real_oos_emits_readable_scorecard(tmp_path):
    result = run_real_oos_validation(output_dir=tmp_path, allow_network=False)
    scorecard = tmp_path / "scorecard.md"
    assert scorecard.exists()
    text = scorecard.read_text(encoding="utf-8")
    assert "因子验证记分卡" in text
    assert "相关性矩阵" in text
    assert (tmp_path / "factor_scorecard.csv").exists()
    assert (tmp_path / "value_matrix.csv").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_oos_real.py::test_real_oos_emits_readable_scorecard -q`
Expected: FAIL — `scorecard.md` does not exist.

- [ ] **Step 3: Add the scorecard build+write to the runner**

In `scripts/run_oos_alpha_validation.py`, add imports near the other `research_platform` imports:

```python
from research_platform.scorecard import (
    build_correlation_views,
    build_factor_scorecard,
    build_group_backtest,
    build_portfolio_scorecard,
    render_scorecard_markdown,
    write_scorecard,
)
```

Then in `run_real_oos_validation`, immediately after `write_oos_report(tables, quality, metadata, output_dir)` and before `return`, insert:

```python
    factor_tbl = build_factor_scorecard(
        factors, forward_returns, experiment.scores["raw"].index,
        n_groups=5, min_names=config.min_names, horizon=config.horizon,
    )
    portfolio_tbl = build_portfolio_scorecard(
        experiment, forward_returns, industry, min_names=config.min_names
    )
    group_tbl = build_group_backtest(
        {"composite": experiment.scores["raw"]}, forward_returns,
        min_names=config.min_names, horizon=config.horizon,
    )
    corr = build_correlation_views(
        factors, forward_returns, experiment.scores["raw"].index, min_names=config.min_names
    )
    scorecard_tables = {
        "factor_scorecard": factor_tbl,
        "portfolio_scorecard": portfolio_tbl,
        "group_backtest": group_tbl,
        "value_matrix": corr["value_matrix"],
        "ic_matrix": corr["ic_matrix"],
        "clusters": corr["clusters"],
    }
    markdown = render_scorecard_markdown(scorecard_tables)
    write_scorecard(scorecard_tables, markdown, output_dir)
```

Note: `OOSConfig` exposes `min_names` and `horizon` (not `groups`), so the group count is the literal `n_groups=5` as shown above.

- [ ] **Step 4: Run the real test to verify it passes**

Run: `python -m pytest tester/test_oos_real.py -q`
Expected: PASS (all tests in file, including reproducibility and the new scorecard test)

- [ ] **Step 5: Eyeball the output**

Run: `python -c "import pathlib,glob; p=sorted(glob.glob('outputs/oos_alpha_improvement/scorecard.md')); print(pathlib.Path(p[0]).read_text()[:1500] if p else 'run main() first')"`
Then run the runner once for a real artifact:
Run: `python scripts/run_oos_alpha_validation.py >/dev/null && sed -n '1,40p' outputs/oos_alpha_improvement/scorecard.md`
Expected: readable markdown tables, correlation matrix as a square, "无判决" note.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_oos_alpha_validation.py tester/test_oos_real.py
git commit -m "feat(scorecard): emit readable scorecard from OOS runner"
```

---

## Task 8: Full regression

- [ ] **Step 1: Run the scorecard unit tests**

Run: `python -m pytest tester/test_scorecard.py -q`
Expected: PASS (9 passed)

- [ ] **Step 2: Run the fast unit suite (exclude slow real/integration)**

Run: `python -m pytest tester -q --ignore=tester/test_backtester.py --ignore=tester/test_oos_real.py --ignore=tester/test_research_platform_real.py --ignore=tester/test_alpha101_correlation_real.py`
Expected: PASS

- [ ] **Step 3: Run the three real-runner tests**

Run: `python -m pytest tester/test_oos_real.py tester/test_research_platform_real.py tester/test_alpha101_correlation_real.py -q`
Expected: PASS

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A && git commit -m "test(scorecard): green regression"
```

---

## Self-Review Notes

- **Spec coverage:** 因子层指标 → Task 2; 组合层 sortino/calmar/win_rate + regime + exposure → Task 3; 分组回测聚合 → Task 4; 相关性方阵 + 聚类 → Task 5; 人读 markdown + CSV 副本 → Task 6; 接入 OOS runner → Task 7; 全量回归 → Task 8. No verdict logic anywhere (matches decision).
- **Deviation from spec:** group backtest reports `mean_forward_return` + non-overlapping `cumulative_return` instead of naive overlapping compounding (overlap honesty; documented in the builder docstring).
- **Type consistency:** builder names and returned column names are consistent across tasks; `build_correlation_views` returns keys `value_matrix`/`ic_matrix`/`clusters` consumed verbatim in Task 7; `matrix_tables` default matches the matrix keys.
