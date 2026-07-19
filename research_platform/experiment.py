"""Composition root for reproducible factor experiments."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ExperimentConfig
from .contracts import ExperimentResult
from .evaluation import evaluate_ic, run_quantile_backtest
from .portfolio import build_long_short_weights, simulate_portfolio


@dataclass(frozen=True)
class ExperimentInputs:
    variants: dict[str, pd.DataFrame]
    forward_returns: pd.DataFrame
    industry: pd.DataFrame
    data_fingerprint: str


def _portfolio_targets(
    panel: pd.DataFrame,
    industry: pd.DataFrame,
    config: ExperimentConfig,
) -> tuple[pd.DataFrame, int]:
    targets = pd.DataFrame(0.0, index=panel.index, columns=panel.columns)
    invalid = 0
    for date in panel.index:
        try:
            targets.loc[date] = build_long_short_weights(
                panel.loc[date],
                quantile=config.quantile,
                max_weight=config.max_weight,
                industry=industry.reindex(index=panel.index, columns=panel.columns).loc[date],
            )
        except ValueError:
            invalid += 1
    return targets, invalid


def run_experiment(
    config: ExperimentConfig,
    inputs: ExperimentInputs,
) -> ExperimentResult:
    if not inputs.variants:
        raise ValueError("at least one factor variant is required")
    industry = inputs.industry.reindex_like(inputs.forward_returns)
    coverage = float(industry.notna().to_numpy().mean()) if industry.size else 0.0
    diagnostics = []
    portfolio_rows = []
    metrics = {}

    for name, panel in inputs.variants.items():
        aligned = panel.reindex_like(inputs.forward_returns)
        ic = evaluate_ic(
            aligned,
            inputs.forward_returns,
            method="spearman",
            min_names=config.min_names,
        )
        quantile = run_quantile_backtest(
            aligned,
            inputs.forward_returns,
            n_groups=config.groups,
            min_names=config.min_names,
        )
        targets, invalid_dates = _portfolio_targets(aligned, industry, config)
        portfolio = simulate_portfolio(targets, inputs.forward_returns, cost_bps=config.cost_bps)
        row = {
            "variant": name,
            "ic_mean": float(ic.mean()) if len(ic) else np.nan,
            "ic_std": float(ic.std(ddof=1)) if len(ic) >= 2 else np.nan,
            "ic_observations": int(ic.count()),
            "top_bottom_mean": float(quantile.spread.mean()) if len(quantile.spread) else np.nan,
        }
        diagnostics.append(row)
        portfolio_rows.append({"variant": name, **portfolio.metrics, "invalid_weight_dates": invalid_dates})
        metrics[name] = row

    gates = {
        "classification_coverage": coverage >= config.classification_coverage,
        "variants_present": {"raw", "neutralized"}.issubset(inputs.variants),
    }
    return ExperimentResult(
        metrics=metrics,
        tables={
            "factor_diagnostics": pd.DataFrame(diagnostics),
            "portfolio_metrics": pd.DataFrame(portfolio_rows),
        },
        quality={"classification_coverage": coverage, "gates": gates},
        metadata={
            "data_fingerprint": inputs.data_fingerprint,
            "config": config.to_dict(),
        },
    )

