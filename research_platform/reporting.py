"""Atomic structured output for experiment results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

from .contracts import ExperimentResult
from .evaluation import evaluate_ic, group_stratification_table
from .portfolio import capacity_curve, simulate_portfolio
from .regime import calendar_year_labels, group_daily_ic, ic_stability_summary


def _json_default(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _atomic_text(path: Path, content: str) -> None:
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _markdown_report(result: ExperimentResult) -> str:
    lines = ["# Factor Research Experiment", "", "## Quality gates", ""]
    for name, passed in result.quality.get("gates", {}).items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## Data quality observations", ""])
    for name, value in result.quality.items():
        if name == "gates":
            continue
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Factor diagnostics", ""])
    diagnostics = result.tables.get("factor_diagnostics", pd.DataFrame())
    lines.append(diagnostics.to_markdown(index=False) if not diagnostics.empty else "No diagnostics generated.")
    lines.extend(["", "## Portfolio metrics", ""])
    portfolios = result.tables.get("portfolio_metrics", pd.DataFrame())
    lines.append(portfolios.to_markdown(index=False) if not portfolios.empty else "No portfolio metrics generated.")
    return "\n".join(lines) + "\n"


def write_result(result: ExperimentResult, output_dir: str | Path) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "experiment_summary.json"
    diagnostics_path = output / "factor_diagnostics.csv"
    portfolio_path = output / "portfolio_metrics.csv"
    quality_path = output / "quality_report.json"
    report_path = output / "report.md"

    _atomic_text(
        summary_path,
        json.dumps(
            {"metrics": result.metrics, "metadata": result.metadata},
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
    )
    _atomic_text(
        diagnostics_path,
        result.tables.get("factor_diagnostics", pd.DataFrame()).to_csv(index=False),
    )
    _atomic_text(
        portfolio_path,
        result.tables.get("portfolio_metrics", pd.DataFrame()).to_csv(index=False),
    )
    _atomic_text(
        quality_path,
        json.dumps(result.quality, indent=2, sort_keys=True, default=_json_default),
    )
    _atomic_text(report_path, _markdown_report(result))
    return [summary_path, diagnostics_path, portfolio_path, quality_path, report_path]


def evaluate_oos_gates(
    raw_sharpe: float,
    soft_sharpe: float,
    baseline_annual_cost: float,
    soft_annual_cost: float,
    soft_max_industry_exposure: float,
    raw_ic: float,
    soft_ic: float,
    label_overlap_count: int,
) -> dict[str, bool]:
    """Evaluate the approved immutable OOS research thresholds."""
    cost_reduction = (
        1.0 - soft_annual_cost / baseline_annual_cost
        if baseline_annual_cost > 0
        else np.nan
    )
    ic_retention = soft_ic / raw_ic if raw_ic > 0 else np.nan
    return {
        "soft_sharpe_beats_raw": bool(soft_sharpe > raw_sharpe),
        "annual_cost_reduction_at_least_40pct": bool(cost_reduction >= 0.40),
        "industry_exposure_at_most_8pct": bool(
            soft_max_industry_exposure <= 0.08
        ),
        "soft_ic_retention_at_least_80pct": bool(ic_retention >= 0.80),
        "zero_label_overlap": bool(label_overlap_count == 0),
    }


def industry_exposure_table(
    weights: pd.DataFrame, industry: pd.DataFrame
) -> pd.DataFrame:
    """Aggregate executed portfolio weights into daily net industry exposure."""
    classification = industry.reindex(index=weights.index, columns=weights.columns)
    rows = []
    for date in weights.index:
        frame = pd.concat(
            {
                "weight": weights.loc[date],
                "industry": classification.loc[date],
            },
            axis=1,
        ).dropna()
        grouped = frame.groupby("industry")["weight"].sum().to_dict()
        grouped["date"] = date
        grouped["max_abs_industry"] = (
            max(abs(value) for value in grouped.values() if not isinstance(value, pd.Timestamp))
            if len(grouped) > 1
            else np.nan
        )
        rows.append(grouped)
    result = pd.DataFrame(rows).set_index("date").fillna(0.0)
    result.index.name = None
    return result


def _return_metrics(returns: pd.Series) -> dict[str, float]:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    annual_return = float(clean.mean() * 252) if len(clean) else np.nan
    annual_volatility = (
        float(clean.std(ddof=1) * np.sqrt(252)) if len(clean) >= 2 else np.nan
    )
    return {
        "annualized_return": annual_return,
        "annualized_volatility": annual_volatility,
        "sharpe": annual_return / annual_volatility
        if annual_volatility and annual_volatility > 0
        else np.nan,
    }


def build_oos_report_tables(
    result,
    forward_returns: pd.DataFrame,
    asset_returns: pd.DataFrame,
    industry: pd.DataFrame,
    baseline_annual_cost: float,
    cost_stress_bps=(0.0, 5.0, 10.0, 20.0),
    min_names: int = 30,
    adv_dollar: pd.DataFrame | None = None,
    aum_grid: tuple[float, ...] = (1e6, 1e7, 1e8, 1e9),
    n_groups: int = 5,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build comparable OOS performance, cost, and exposure diagnostics.

    When ``adv_dollar`` (per-name dollar ADV) is supplied, a ``capacity`` table
    sweeps each path's net Sharpe across ``aum_grid`` using the square-root
    liquidity-impact model, exposing where scaling capital erodes the signal.
    A ``regime_stability`` table always summarizes each path's IC sign
    consistency across calendar-year subperiods.
    """
    ic_by_path = {
        path: evaluate_ic(panel, forward_returns, min_names=min_names)
        for path, panel in result.scores.items()
    }
    fold_rows = []
    for fold in result.folds:
        for path, portfolio in result.portfolios.items():
            metrics = _return_metrics(portfolio.net_returns.reindex(fold.test_dates))
            fold_ic = ic_by_path[path].reindex(fold.test_dates).dropna()
            fold_rows.append(
                {
                    "fold": fold.number,
                    "path": path,
                    "start": fold.test_dates[0],
                    "end": fold.test_dates[-1],
                    "ic_mean": float(fold_ic.mean()) if len(fold_ic) else np.nan,
                    **metrics,
                }
            )

    year_rows = []
    years = sorted({date.year for date in result.scores["raw"].index})
    for year in years:
        year_dates = result.scores["raw"].index[
            result.scores["raw"].index.year == year
        ]
        for path, portfolio in result.portfolios.items():
            year_ic = ic_by_path[path].reindex(year_dates).dropna()
            year_rows.append(
                {
                    "year": year,
                    "path": path,
                    "ic_mean": float(year_ic.mean()) if len(year_ic) else np.nan,
                    **_return_metrics(portfolio.net_returns.reindex(year_dates)),
                }
            )

    cost_rows = []
    aligned_returns = asset_returns.reindex(
        index=result.scores["raw"].index,
        columns=result.scores["raw"].columns,
    )
    for cost_bps in cost_stress_bps:
        for path, targets in result.portfolio_targets.items():
            stressed = simulate_portfolio(targets, aligned_returns, cost_bps=cost_bps)
            cost_rows.append(
                {"cost_bps": float(cost_bps), "path": path, **stressed.metrics}
            )

    exposure_parts = []
    exposure_summary = {}
    for path, portfolio in result.portfolios.items():
        exposure = industry_exposure_table(portfolio.weights, industry)
        exposure.insert(0, "path", path)
        exposure.insert(1, "date", exposure.index)
        exposure_parts.append(exposure.reset_index(drop=True))
        exposure_summary[path] = float(exposure["max_abs_industry"].mean())

    raw_ic = float(ic_by_path["raw"].mean())
    soft_ic = float(ic_by_path["soft"].mean())
    soft_portfolio = result.portfolios["soft"]
    soft_annual_cost = float(soft_portfolio.turnover.mean() * 252 * 10.0 / 10_000)
    gates = evaluate_oos_gates(
        raw_sharpe=float(result.portfolios["raw"].metrics["sharpe"]),
        soft_sharpe=float(soft_portfolio.metrics["sharpe"]),
        baseline_annual_cost=baseline_annual_cost,
        soft_annual_cost=soft_annual_cost,
        soft_max_industry_exposure=exposure_summary["soft"],
        raw_ic=raw_ic,
        soft_ic=soft_ic,
        label_overlap_count=int(result.quality["label_overlap_count"]),
    )
    year_labels = calendar_year_labels(result.scores["raw"].index)
    regime_rows = []
    for path in result.scores:
        subperiod_ic = group_daily_ic(ic_by_path[path], year_labels)
        regime_rows.append({"path": path, **ic_stability_summary(subperiod_ic)})

    tables = {
        "fold_metrics": pd.DataFrame(fold_rows),
        "year_metrics": pd.DataFrame(year_rows),
        "cost_stress": pd.DataFrame(cost_rows),
        "industry_exposure": pd.concat(exposure_parts, ignore_index=True),
        "regime_stability": pd.DataFrame(regime_rows),
        "group_stratification": group_stratification_table(
            result.scores, forward_returns, n_groups=n_groups, min_names=min_names
        ),
    }
    if adv_dollar is not None:
        capacity_parts = []
        for path, targets in result.portfolio_targets.items():
            curve = capacity_curve(targets, aligned_returns, adv_dollar, aum_grid=aum_grid)
            curve.insert(0, "path", path)
            capacity_parts.append(curve)
        tables["capacity"] = pd.concat(capacity_parts, ignore_index=True)
    quality = {
        **result.quality,
        "raw_ic": raw_ic,
        "soft_ic": soft_ic,
        "soft_ic_retention": soft_ic / raw_ic if raw_ic > 0 else np.nan,
        "baseline_annual_cost": baseline_annual_cost,
        "soft_annual_cost": soft_annual_cost,
        "soft_average_max_industry_exposure": exposure_summary["soft"],
        "gates": gates,
    }
    return tables, quality


def _oos_markdown(quality: dict, tables: dict[str, pd.DataFrame]) -> str:
    lines = ["# OOS Alpha Improvement Report", "", "## Acceptance gates", ""]
    for name, passed in quality.get("gates", {}).items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## Quality observations", ""])
    for name, value in quality.items():
        if name != "gates":
            lines.append(f"- {name}: {value}")
    for name, table in tables.items():
        title = name.replace("_", " ").title()
        lines.extend(["", f"## {title}", ""])
        lines.append(table.to_markdown(index=False) if not table.empty else "No rows.")
    return "\n".join(lines) + "\n"


def write_oos_report(
    tables: dict[str, pd.DataFrame],
    quality: dict,
    metadata: dict,
    output_dir: str | Path,
) -> list[Path]:
    """Atomically write all OOS diagnostics and immutable gate outcomes."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in sorted(tables):
        path = output / f"{name}.csv"
        _atomic_text(path, tables[name].to_csv(index=False))
        paths.append(path)
    quality_path = output / "quality_report.json"
    metadata_path = output / "metadata.json"
    report_path = output / "report.md"
    _atomic_text(
        quality_path,
        json.dumps(quality, indent=2, sort_keys=True, default=_json_default),
    )
    _atomic_text(
        metadata_path,
        json.dumps(metadata, indent=2, sort_keys=True, default=_json_default),
    )
    _atomic_text(report_path, _oos_markdown(quality, tables))
    return paths + [quality_path, metadata_path, report_path]
