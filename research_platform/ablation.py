"""Shared-fold A/B/C orchestration for correlation-aware factor research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .evaluation import evaluate_ic, generate_purged_folds
from .oos import (
    OOSConfig,
    OOSExperimentResult,
    OOSFoldState,
    _label_overlap_count,
    compose_oos_score,
    run_oos_experiment,
)
from .portfolio import build_buffered_targets, simulate_portfolio
from .preprocessing import build_industry_score_variants_panel
from .selection import select_correlation_aware_factors


@dataclass(frozen=True)
class AblationConfig:
    oos: OOSConfig = field(default_factory=OOSConfig)
    min_coverage: float = 0.80
    min_block_observations: int = 20
    hard_threshold: float = 0.75
    min_pair_dates: int = 60
    ic_shrinkage: float = 0.50
    ic_soft_threshold: float = 0.25
    min_factors: int = 5
    max_factors: int = 6

    def to_dict(self) -> dict[str, object]:
        correlation = asdict(self)
        correlation.pop("oos")
        return {"oos": self.oos.to_dict(), "correlation": correlation}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AblationConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise TypeError("ablation config must be a YAML mapping")
        oos_payload = payload.get("oos", {})
        correlation_payload = payload.get("correlation", {})
        if not isinstance(oos_payload, dict) or not isinstance(correlation_payload, dict):
            raise TypeError("oos and correlation config sections must be mappings")
        if "cost_stress_bps" in oos_payload:
            oos_payload = {
                **oos_payload,
                "cost_stress_bps": tuple(oos_payload["cost_stress_bps"]),
            }
        return cls(oos=OOSConfig(**oos_payload), **correlation_payload)


@dataclass(frozen=True)
class AblationArmResult:
    name: str
    candidate_names: tuple[str, ...]
    experiment: OOSExperimentResult
    fold_diagnostics: pd.DataFrame


@dataclass(frozen=True)
class AblationResult:
    arms: dict[str, AblationArmResult]
    shared_dates: pd.Index
    quality: dict[str, object]


def _baseline_fold_diagnostics(experiment: OOSExperimentResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fold": fold.number,
                "valid": bool(fold.selection.weights),
                "selected_count": len(fold.selection.selected),
                "selected": "|".join(fold.selection.selected),
                "selector": "stability",
            }
            for fold in experiment.folds
        ]
    )


def _run_correlation_arm(
    name: str,
    factors: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    industry: pd.DataFrame,
    config: AblationConfig,
) -> AblationArmResult:
    oos = config.oos
    dates = close.index
    columns = close.columns
    forward = close.shift(-oos.horizon).div(close).sub(1.0)
    asset_returns = close.pct_change(fill_method=None)
    fold_slices = generate_purged_folds(
        dates,
        min_train=oos.min_train,
        test_size=oos.test_size,
        step=oos.step,
        horizon=oos.horizon,
        purge=oos.purge,
    )
    if not fold_slices:
        raise ValueError("not enough dates to create an OOS fold")

    folds = []
    path_parts = {path: [] for path in ("raw", "soft", "strict")}
    variant_diagnostics = []
    fold_rows = []
    overlap_count = 0
    for number, (train_dates, test_dates) in enumerate(fold_slices):
        ic_table = pd.DataFrame(
            {
                factor: evaluate_ic(
                    panel.loc[train_dates],
                    forward.loc[train_dates],
                    min_names=oos.min_names,
                )
                for factor, panel in factors.items()
            },
            index=train_dates,
        )
        selection = select_correlation_aware_factors(
            factors,
            ic_table,
            train_dates,
            min_abs_ic=oos.min_abs_ic,
            min_coverage=config.min_coverage,
            min_names=oos.min_names,
            min_block_observations=config.min_block_observations,
            hard_threshold=config.hard_threshold,
            min_pair_dates=config.min_pair_dates,
            ic_shrinkage=config.ic_shrinkage,
            ic_soft_threshold=config.ic_soft_threshold,
            rebalance_interval=oos.rebalance_interval,
            min_factors=config.min_factors,
            max_factors=config.max_factors,
            max_weight=oos.factor_weight_cap,
        )
        folds.append(OOSFoldState(number, train_dates, test_dates, selection))
        overlap_count += _label_overlap_count(
            dates, train_dates, test_dates, oos.horizon
        )
        raw_score = compose_oos_score(factors, test_dates, selection)
        variants = build_industry_score_variants_panel(
            raw_score,
            industry.reindex(index=test_dates, columns=columns),
            soft_strength=oos.soft_strength,
            min_names=oos.min_names,
        )
        for path in path_parts:
            path_parts[path].append(getattr(variants, path))
        diagnostics = variants.diagnostics.copy()
        diagnostics.insert(0, "fold", number)
        variant_diagnostics.append(diagnostics)
        fold_rows.append(
            {
                "fold": number,
                "valid": selection.valid,
                "selected_count": len(selection.selected),
                "selected": "|".join(selection.selected),
                "selector": "correlation_aware",
                "action": "score" if selection.valid else "invalid_fold_hold",
            }
        )

    scores = {path: pd.concat(parts).sort_index() for path, parts in path_parts.items()}
    targets = {}
    target_diagnostics = {}
    portfolios = {}
    aligned_returns = asset_returns.reindex(index=scores["raw"].index, columns=columns)
    for path, score in scores.items():
        buffered = build_buffered_targets(
            score,
            rebalance_interval=oos.rebalance_interval,
            entry_quantile=oos.entry_quantile,
            exit_quantile=oos.exit_quantile,
            max_weight=oos.name_weight_cap,
        )
        targets[path] = buffered.targets
        target_diagnostics[path] = buffered.diagnostics
        portfolios[path] = simulate_portfolio(
            buffered.targets, aligned_returns, cost_bps=oos.cost_bps
        )

    selection_tables = [
        fold.selection.diagnostics.assign(fold=fold.number) for fold in folds
    ]
    experiment = OOSExperimentResult(
        folds=tuple(folds),
        scores=scores,
        portfolio_targets=targets,
        portfolio_diagnostics=target_diagnostics,
        portfolios=portfolios,
        diagnostics={
            "score_variants": pd.concat(variant_diagnostics).sort_index(),
            "fold_selection": pd.concat(selection_tables, names=["fold_row"]),
        },
        quality={"label_overlap_count": overlap_count},
    )
    return AblationArmResult(
        name=name,
        candidate_names=tuple(factors),
        experiment=experiment,
        fold_diagnostics=pd.DataFrame(fold_rows),
    )


def run_alpha101_correlation_ablation(
    existing_factors: dict[str, pd.DataFrame],
    alpha101_factors: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    industry: pd.DataFrame,
    config: AblationConfig | None = None,
) -> AblationResult:
    """Run baseline, selector-only, and expanded-pool arms on shared folds."""
    config = AblationConfig() if config is None else config
    overlap = set(existing_factors).intersection(alpha101_factors)
    if overlap:
        raise ValueError(f"duplicate candidate names across pools: {sorted(overlap)}")
    arm_a_experiment = run_oos_experiment(
        existing_factors, close, industry, config=config.oos
    )
    arm_a = AblationArmResult(
        name="A",
        candidate_names=tuple(existing_factors),
        experiment=arm_a_experiment,
        fold_diagnostics=_baseline_fold_diagnostics(arm_a_experiment),
    )
    arm_b = _run_correlation_arm(
        "B", existing_factors, close, industry, config
    )
    expanded = {**existing_factors, **alpha101_factors}
    arm_c = _run_correlation_arm("C", expanded, close, industry, config)
    arms = {"A": arm_a, "B": arm_b, "C": arm_c}
    fold_signatures = {
        name: tuple(
            (tuple(fold.train_dates), tuple(fold.test_dates))
            for fold in arm.experiment.folds
        )
        for name, arm in arms.items()
    }
    shared = len(set(fold_signatures.values())) == 1
    return AblationResult(
        arms=arms,
        shared_dates=arm_a_experiment.scores["raw"].index,
        quality={
            "shared_folds": shared,
            "label_overlap_count": {
                name: int(arm.experiment.quality["label_overlap_count"])
                for name, arm in arms.items()
            },
        },
    )
