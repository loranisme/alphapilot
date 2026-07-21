"""Structured diagnostics and immutable gates for the Alpha101 ablation."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .ablation import AblationConfig, AblationResult
from .evaluation import evaluate_ic
from .portfolio import simulate_portfolio
from .reporting import _atomic_text, _json_default, industry_exposure_table
from .selection import CorrelationSelectionResult


TABLE_ORDER = (
    "factor_value_correlation",
    "ic_correlation",
    "correlation_clusters",
    "factor_selection_by_fold",
    "candidate_coverage",
    "ablation_metrics",
    "fold_metrics",
    "year_metrics",
    "cost_stress",
    "industry_exposure",
)


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
        if annual_volatility > 0
        else np.nan,
    }


def _matrix_rows(
    arm: str,
    fold: int,
    estimate,
) -> list[dict[str, object]]:
    rows = []
    for left in estimate.values.index:
        for right in estimate.values.columns:
            rows.append(
                {
                    "arm": arm,
                    "fold": fold,
                    "left": left,
                    "right": right,
                    "correlation": estimate.values.loc[left, right],
                    "verified": bool(estimate.verified.loc[left, right]),
                    "valid_dates": int(estimate.counts.loc[left, right]),
                }
            )
    return rows


def _selected_pair_gate(result: AblationResult, threshold: float) -> bool:
    for arm_name in ("B", "C"):
        for fold in result.arms[arm_name].experiment.folds:
            selection = fold.selection
            if not isinstance(selection, CorrelationSelectionResult) or not selection.valid:
                continue
            selected = list(selection.selected)
            for position, left in enumerate(selected):
                for right in selected[position + 1 :]:
                    if not bool(selection.factor_value_correlation.verified.loc[left, right]):
                        return False
                    value = selection.factor_value_correlation.values.loc[left, right]
                    if not np.isfinite(value) or abs(float(value)) >= threshold:
                        return False
    return True


def build_ablation_report(
    result: AblationResult,
    forward_returns: pd.DataFrame,
    asset_returns: pd.DataFrame,
    industry: pd.DataFrame,
    config: AblationConfig,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """Build all fixed A/B/C diagnostics and acceptance gates."""
    oos = config.oos
    ic_by_arm_path = {
        (arm_name, path): evaluate_ic(
            panel,
            forward_returns.reindex(index=panel.index, columns=panel.columns),
            min_names=oos.min_names,
        )
        for arm_name, arm in result.arms.items()
        for path, panel in arm.experiment.scores.items()
    }

    ablation_rows = []
    fold_rows = []
    year_rows = []
    cost_rows = []
    exposure_parts = []
    exposure_summary: dict[tuple[str, str], float] = {}
    positive_folds: dict[tuple[str, str], int] = {}
    aligned_returns = asset_returns.reindex(
        index=result.shared_dates, columns=next(iter(result.arms.values())).experiment.scores["raw"].columns
    )

    for arm_name, arm in result.arms.items():
        experiment = arm.experiment
        for path, portfolio in experiment.portfolios.items():
            ic = ic_by_arm_path[(arm_name, path)]
            fold_ic_means = []
            for fold in experiment.folds:
                fold_ic = ic.reindex(fold.test_dates).dropna()
                ic_mean = float(fold_ic.mean()) if len(fold_ic) else np.nan
                fold_ic_means.append(ic_mean)
                fold_rows.append(
                    {
                        "arm": arm_name,
                        "fold": fold.number,
                        "path": path,
                        "start": fold.test_dates[0],
                        "end": fold.test_dates[-1],
                        "ic_mean": ic_mean,
                        **_return_metrics(portfolio.net_returns.reindex(fold.test_dates)),
                    }
                )
            positive_folds[(arm_name, path)] = sum(
                np.isfinite(value) and value > 0 for value in fold_ic_means
            )
            ablation_rows.append(
                {
                    "arm": arm_name,
                    "path": path,
                    "ic_mean": float(ic.mean()),
                    "positive_ic_folds": positive_folds[(arm_name, path)],
                    **portfolio.metrics,
                }
            )

            for year in sorted(set(experiment.scores[path].index.year)):
                year_dates = experiment.scores[path].index[
                    experiment.scores[path].index.year == year
                ]
                year_ic = ic.reindex(year_dates).dropna()
                year_rows.append(
                    {
                        "arm": arm_name,
                        "year": year,
                        "path": path,
                        "ic_mean": float(year_ic.mean()) if len(year_ic) else np.nan,
                        **_return_metrics(portfolio.net_returns.reindex(year_dates)),
                    }
                )

            exposure = industry_exposure_table(
                portfolio.weights,
                industry.reindex(index=portfolio.weights.index, columns=portfolio.weights.columns),
            )
            exposure_summary[(arm_name, path)] = float(
                exposure["max_abs_industry"].mean()
            )
            exposure = exposure.reset_index(names="date")
            exposure.insert(0, "path", path)
            exposure.insert(0, "arm", arm_name)
            exposure_parts.append(exposure)

        for cost_bps in oos.cost_stress_bps:
            for path, targets in experiment.portfolio_targets.items():
                stressed = simulate_portfolio(
                    targets,
                    aligned_returns.reindex(index=targets.index, columns=targets.columns),
                    cost_bps=float(cost_bps),
                )
                cost_rows.append(
                    {
                        "arm": arm_name,
                        "path": path,
                        "cost_bps": float(cost_bps),
                        **stressed.metrics,
                    }
                )

    value_corr_rows = []
    ic_corr_rows = []
    cluster_parts = []
    selection_rows = []
    coverage_parts = []
    for arm_name, arm in result.arms.items():
        for fold in arm.experiment.folds:
            selection = fold.selection
            diagnostics = selection.diagnostics.reset_index().rename(
                columns={selection.diagnostics.index.name or "index": "factor"}
            )
            diagnostics.insert(0, "fold", fold.number)
            diagnostics.insert(0, "arm", arm_name)
            coverage_parts.append(diagnostics)
            for factor in selection.selected:
                selection_rows.append(
                    {
                        "arm": arm_name,
                        "fold": fold.number,
                        "valid": getattr(selection, "valid", bool(selection.weights)),
                        "factor": factor,
                        "direction": selection.directions.get(factor),
                        "weight": selection.weights.get(factor),
                    }
                )
            if isinstance(selection, CorrelationSelectionResult):
                value_corr_rows.extend(
                    _matrix_rows(
                        arm_name, fold.number, selection.factor_value_correlation
                    )
                )
                ic_corr_rows.extend(
                    _matrix_rows(arm_name, fold.number, selection.ic_correlation)
                )
                clusters = selection.clusters.copy()
                clusters.insert(0, "fold", fold.number)
                clusters.insert(0, "arm", arm_name)
                cluster_parts.append(clusters)

    tables = {
        "factor_value_correlation": pd.DataFrame(value_corr_rows),
        "ic_correlation": pd.DataFrame(ic_corr_rows),
        "correlation_clusters": pd.concat(cluster_parts, ignore_index=True)
        if cluster_parts
        else pd.DataFrame(),
        "factor_selection_by_fold": pd.DataFrame(selection_rows),
        "candidate_coverage": pd.concat(coverage_parts, ignore_index=True),
        "ablation_metrics": pd.DataFrame(ablation_rows),
        "fold_metrics": pd.DataFrame(fold_rows),
        "year_metrics": pd.DataFrame(year_rows),
        "cost_stress": pd.DataFrame(cost_rows),
        "industry_exposure": pd.concat(exposure_parts, ignore_index=True),
    }
    for name, table in tables.items():
        sort_columns = [
            column
            for column in ("arm", "fold", "year", "path", "cost_bps", "factor", "left", "right", "date")
            if column in table.columns
        ]
        if sort_columns:
            tables[name] = table.sort_values(sort_columns).reset_index(drop=True)

    metrics = tables["ablation_metrics"].set_index(["arm", "path"])
    a_raw = metrics.loc[("A", "raw")]
    a_soft = metrics.loc[("A", "soft")]
    c_raw = metrics.loc[("C", "raw")]
    c_soft = metrics.loc[("C", "soft")]
    research_gates = {
        "c_raw_ic_not_below_a": bool(c_raw["ic_mean"] >= a_raw["ic_mean"]),
        "c_soft_sharpe_above_a": bool(c_soft["sharpe"] > a_soft["sharpe"]),
        "c_soft_turnover_not_above_a": bool(
            c_soft["average_turnover"] <= a_soft["average_turnover"]
        ),
        "c_positive_ic_folds_at_least_a": bool(
            c_raw["positive_ic_folds"] >= a_raw["positive_ic_folds"]
        ),
        "c_soft_industry_exposure_at_most_8pct": bool(
            exposure_summary[("C", "soft")] <= oos.max_industry_exposure
        ),
    }
    correlation_fold_sizes_valid = all(
        (not bool(row.valid))
        or config.min_factors <= int(row.selected_count) <= config.max_factors
        for arm_name in ("B", "C")
        for row in result.arms[arm_name].fold_diagnostics.itertuples()
    )
    expected_costs = set(map(float, oos.cost_stress_bps))
    complete_cost_grid = all(
        set(
            tables["cost_stress"].loc[
                (tables["cost_stress"]["arm"] == arm)
                & (tables["cost_stress"]["path"] == path),
                "cost_bps",
            ]
        )
        == expected_costs
        for arm in result.arms
        for path in ("raw", "soft", "strict")
    )
    engineering_gates = {
        "shared_folds": bool(result.quality["shared_folds"]),
        "zero_label_overlap": all(
            int(value) == 0
            for value in result.quality["label_overlap_count"].values()
        ),
        "valid_correlation_fold_size_5_to_6": correlation_fold_sizes_valid,
        "no_selected_pair_breaches_hard_threshold": _selected_pair_gate(
            result, config.hard_threshold
        ),
        "complete_cost_stress_grid": complete_cost_grid,
    }
    quality = {
        "research_gates": research_gates,
        "engineering_gates": engineering_gates,
        "research_pass": all(research_gates.values()),
        "engineering_pass": all(engineering_gates.values()),
        "positive_ic_folds": {
            f"{arm}_{path}": count
            for (arm, path), count in positive_folds.items()
        },
        "average_max_industry_exposure": {
            f"{arm}_{path}": value
            for (arm, path), value in exposure_summary.items()
        },
    }
    return tables, quality


def _markdown_report(
    tables: dict[str, pd.DataFrame], quality: dict[str, object], metadata: dict
) -> str:
    lines = ["# Alpha101 Correlation-Aware OOS Report", "", "## Engineering gates", ""]
    for name, passed in quality["engineering_gates"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## Research gates", ""])
    for name, passed in quality["research_gates"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## A/B/C metrics", ""])
    lines.append(tables["ablation_metrics"].to_markdown(index=False))
    selected = tables["factor_selection_by_fold"]
    alpha_selected = selected[selected["factor"].str.startswith("alpha101_", na=False)]
    lines.extend(["", "## Alpha101 candidates selected OOS", ""])
    lines.append(alpha_selected.to_markdown(index=False) if len(alpha_selected) else "No Alpha101 candidate was selected.")
    clusters = tables["correlation_clusters"]
    redundant = clusters[clusters.get("cluster_size", pd.Series(dtype=float)).gt(1)]
    lines.extend(["", "## Redundant clusters", ""])
    lines.append(redundant.to_markdown(index=False) if len(redundant) else "No redundant cluster was found.")
    lines.extend(
        [
            "",
            "## Research boundary",
            "",
            "Research-gate failure is a valid result and does not trigger OOS parameter changes.",
            f"Source: {metadata.get('source', 'not recorded')}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_ablation_report(
    tables: dict[str, pd.DataFrame],
    quality: dict[str, object],
    metadata: dict,
    output_dir: str | Path,
) -> list[Path]:
    """Atomically write the exact approved output contract."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in TABLE_ORDER:
        path = output / f"{name}.csv"
        _atomic_text(path, tables.get(name, pd.DataFrame()).to_csv(index=False))
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
    _atomic_text(report_path, _markdown_report(tables, quality, metadata))
    return paths + [quality_path, metadata_path, report_path]
