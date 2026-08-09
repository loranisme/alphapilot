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


# append to research_platform/scorecard.py
from .correlation import factor_rank_turnover
from .evaluation import evaluate_ic, group_stratification_table


def _sign_direction(mean_ic: float) -> int:
    """+1/-1 orientation from a mean IC; defaults to +1 when undefined (NaN)."""
    return 1 if not np.isfinite(mean_ic) or mean_ic >= 0 else -1


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
        direction = _sign_direction(spear["mean"])
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

    ``run_quantile_backtest`` drops dates outright when a cross-section fails
    ``min_names`` (no NaN row is kept), so positional ``::horizon`` striding on
    its output can drift off calendar-horizon spacing once the universe has
    gaps. ``mean``/``std`` are unaffected by this (overlap doesn't bias them),
    but the non-overlapping ``cumulative_return`` sample is reindexed onto the
    dense ``forward_returns`` date grid before striding so the stride lands on
    true calendar-horizon boundaries.
    """
    scale = np.sqrt(periods_per_year / horizon)
    rows = []
    for name, panel in series.items():
        result = run_quantile_backtest(panel, forward_returns, n_groups=n_groups, min_names=min_names)
        for col in result.group_returns.columns:
            block = pd.to_numeric(result.group_returns[col], errors="coerce").dropna()
            mean = float(block.mean()) if len(block) else np.nan
            std = float(block.std(ddof=1)) if len(block) >= 2 else np.nan
            dense = pd.to_numeric(result.group_returns[col], errors="coerce").reindex(forward_returns.index)
            nonoverlap = dense.iloc[::horizon].dropna()
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
        directions[name] = _sign_direction(mean)
    value_estimate = factor_value_correlation(
        factors, dates, directions, min_names=min_names, min_dates=min_pair_dates
    )
    ic_frame = pd.DataFrame(ic_columns).reindex(index=pd.Index(dates))
    ic_estimate = ic_correlation(ic_frame, directions, shrinkage=ic_shrinkage)
    cluster_rows = connected_correlation_clusters(
        value_estimate.values, value_estimate.verified, threshold=hard_threshold
    )
    clusters = (
        cluster_rows.drop_duplicates(subset="cluster")[["cluster", "cluster_size", "members"]]
        .reset_index(drop=True)
    )
    return {
        "value_matrix": value_estimate.values,
        "ic_matrix": ic_estimate.values,
        "clusters": clusters,
    }


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
